"""
license_manager.py

A small, secure software license manager built on Ed25519 digital signatures.

Model
-----
- The VENDOR holds a *private key* and signs every license they issue.
- The APP ships with the matching *public key* and verifies licenses offline.
- A valid signature proves (a) the license was issued by the vendor and
  (b) its contents (expiry, tier, licensee...) were not modified.

A license is a compact string of the form:

    <base64url(payload_json)>.<base64url(signature)>

This is conceptually similar to a JWT but trimmed to what a license needs.
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature


# --------------------------------------------------------------------------- #
# Helpers: URL-safe base64 without padding (keeps license strings clean)
# --------------------------------------------------------------------------- #
def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Key management
# --------------------------------------------------------------------------- #
def generate_keypair() -> tuple[bytes, bytes]:
    """
    Generate a new Ed25519 keypair.

    Returns (private_pem, public_pem) as PEM-encoded bytes.
    Keep the private key SECRET (vendor side). Distribute only the public key.
    """
    private_key = Ed25519PrivateKey.generate()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def load_private_key(private_pem: bytes) -> Ed25519PrivateKey:
    return serialization.load_pem_private_key(private_pem, password=None)


def load_public_key(public_pem: bytes) -> Ed25519PublicKey:
    return serialization.load_pem_public_key(public_pem)


# --------------------------------------------------------------------------- #
# License payload
# --------------------------------------------------------------------------- #
@dataclass
class LicensePayload:
    licensee: str                 # who the license is for
    product: str                  # product / SKU name
    tier: str = "standard"        # e.g. "standard", "pro", "enterprise"
    license_id: str = ""          # unique id (auto-filled if empty)
    issued_at: str = ""           # ISO-8601 UTC (auto-filled if empty)
    expires_at: Optional[str] = None  # ISO-8601 UTC, or None for perpetual
    max_seats: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LicensePayload":
        # Ignore unknown keys defensively so older apps tolerate newer fields.
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


# --------------------------------------------------------------------------- #
# Create
# --------------------------------------------------------------------------- #
def create_license(
    payload: LicensePayload,
    private_key: Ed25519PrivateKey,
    valid_for: Optional[timedelta] = None,
) -> str:
    """
    Sign a payload and return the license string.

    If `valid_for` is given and the payload has no expires_at, set expiry to
    now + valid_for.
    """
    # Fill in auto fields
    if not payload.license_id:
        payload.license_id = str(uuid.uuid4())
    if not payload.issued_at:
        payload.issued_at = _now().isoformat()
    if payload.expires_at is None and valid_for is not None:
        payload.expires_at = (_now() + valid_for).isoformat()

    # Deterministic serialization so the signed bytes are reproducible.
    payload_json = json.dumps(
        payload.to_dict(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    payload_b64 = _b64encode(payload_json)

    # Sign the ENCODED payload bytes (no canonicalization ambiguity on verify).
    signature = private_key.sign(payload_b64.encode("ascii"))
    sig_b64 = _b64encode(signature)

    return f"{payload_b64}.{sig_b64}"


# --------------------------------------------------------------------------- #
# Verify
# --------------------------------------------------------------------------- #
@dataclass
class VerificationResult:
    valid: bool
    reason: str
    payload: Optional[LicensePayload] = None

    def __bool__(self) -> bool:
        return self.valid


def verify_license(
    license_str: str,
    public_key: Ed25519PublicKey,
    at: Optional[datetime] = None,
) -> VerificationResult:
    """
    Verify signature and validity window of a license string.

    `at` lets you check validity at a specific moment (defaults to now);
    useful for testing.
    """
    now = at or _now()

    # 1. Structural parse
    try:
        payload_b64, sig_b64 = license_str.strip().split(".")
    except ValueError:
        return VerificationResult(False, "malformed: expected '<payload>.<signature>'")

    # 2. Signature check — this is the security-critical step.
    try:
        public_key.verify(_b64decode(sig_b64), payload_b64.encode("ascii"))
    except (InvalidSignature, ValueError):
        return VerificationResult(False, "invalid signature (tampered or wrong key)")

    # 3. Decode payload (safe to trust only AFTER signature verified)
    try:
        data = json.loads(_b64decode(payload_b64))
        payload = LicensePayload.from_dict(data)
    except Exception:
        return VerificationResult(False, "malformed payload")

    # 4. Time-window checks
    try:
        issued = datetime.fromisoformat(payload.issued_at)
        if issued > now + timedelta(minutes=5):  # small clock-skew allowance
            return VerificationResult(False, "license not yet valid", payload)
    except Exception:
        return VerificationResult(False, "invalid issued_at", payload)

    if payload.expires_at is not None:
        try:
            expires = datetime.fromisoformat(payload.expires_at)
        except Exception:
            return VerificationResult(False, "invalid expires_at", payload)
        if now > expires:
            return VerificationResult(False, "license expired", payload)

    return VerificationResult(True, "ok", payload)
