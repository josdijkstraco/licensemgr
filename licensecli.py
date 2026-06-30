#!/usr/bin/env python3
"""
licensecli.py — a command-line wrapper around licensemgr.py.

Subcommands
-----------
  keygen    Generate a vendor Ed25519 keypair (private + public PEM files).
  issue     Sign and emit a license string (vendor side, needs private key).
  verify    Check a license's signature and validity window (app side, public key).
  inspect   Decode a license payload WITHOUT verifying it (debugging only).

Examples
--------
  ./licensecli.py keygen --private vendor.pem --public app_pub.pem
  ./licensecli.py issue --key vendor.pem --licensee "Acme Corp" \\
      --product WidgetPro --tier enterprise --seats 50 --valid-for 365d
  ./licensecli.py verify --key app_pub.pem --in license.txt
  echo "<license>" | ./licensecli.py verify --key app_pub.pem

Exit codes
----------
  0  success / license valid
  1  failure / license invalid
  2  usage error (bad arguments)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# licensemgr.py lives next to this script; make sure it's importable even when
# the CLI is invoked from another directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import licensemgr as lm
except ImportError as exc:  # pragma: no cover - environment guard
    sys.exit(
        f"error: could not import licensemgr.py ({exc}).\n"
        "Make sure licensemgr.py is next to licensecli.py and that the "
        "'cryptography' package is installed (pip install cryptography)."
    )


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
_DURATION_RE = re.compile(r"(?P<value>\d+)\s*(?P<unit>[smhdwy])", re.IGNORECASE)
_UNIT_SECONDS = {
    "s": 1,
    "m": 60,            # minutes
    "h": 3600,
    "d": 86400,
    "w": 604800,
    "y": 31536000,      # 365 days
}


def parse_duration(text: str) -> timedelta:
    """
    Parse a compact duration like '365d', '52w', '24h', '90d12h' into a
    timedelta. Units: s=seconds, m=minutes, h=hours, d=days, w=weeks, y=365d.
    """
    text = text.strip()
    matches = list(_DURATION_RE.finditer(text))
    # Reject input that has leftover junk we didn't consume.
    if not matches or "".join(m.group(0) for m in matches).replace(" ", "") != text.replace(" ", ""):
        raise ValueError(
            f"invalid duration {text!r}; use forms like '365d', '52w', '24h', '90d12h'"
        )
    total = 0
    for m in matches:
        total += int(m.group("value")) * _UNIT_SECONDS[m.group("unit").lower()]
    return timedelta(seconds=total)


def parse_iso(text: str) -> datetime:
    """Parse an ISO-8601 timestamp, tolerating a trailing 'Z' and naive input
    (assumed UTC)."""
    cleaned = text.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def read_key(path: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        sys.exit(f"error: cannot read key file {path!r}: {exc}")


def read_license(args: argparse.Namespace) -> str:
    """Resolve the license string from --license, --in, or stdin."""
    if getattr(args, "license", None):
        return args.license.strip()
    if getattr(args, "in_path", None):
        try:
            return Path(args.in_path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            sys.exit(f"error: cannot read license file {args.in_path!r}: {exc}")
    if not sys.stdin.isatty():
        data = sys.stdin.read().strip()
        if data:
            return data
    sys.exit("error: no license provided (use --license, --in PATH, or pipe via stdin)")


# --------------------------------------------------------------------------- #
# Subcommand: keygen
# --------------------------------------------------------------------------- #
def cmd_keygen(args: argparse.Namespace) -> int:
    priv_path = Path(args.private)
    pub_path = Path(args.public)

    for p in (priv_path, pub_path):
        if p.exists() and not args.force:
            sys.exit(f"error: {p} already exists (use --force to overwrite)")

    private_pem, public_pem = lm.generate_keypair()

    priv_path.write_bytes(private_pem)
    try:
        priv_path.chmod(0o600)  # private key: owner read/write only
    except OSError:
        pass  # non-POSIX filesystem; best effort
    pub_path.write_bytes(public_pem)

    print(f"private key -> {priv_path}  (KEEP SECRET — chmod 600)")
    print(f"public  key -> {pub_path}  (ship this with your app)")
    return 0


# --------------------------------------------------------------------------- #
# Subcommand: issue
# --------------------------------------------------------------------------- #
def cmd_issue(args: argparse.Namespace) -> int:
    private_key = lm.load_private_key(read_key(args.key))

    expires_at = parse_iso(args.expires_at).isoformat() if args.expires_at else None
    valid_for = parse_duration(args.valid_for) if args.valid_for else None

    payload = lm.LicensePayload(
        licensee=args.licensee,
        product=args.product,
        tier=args.tier,
        max_seats=args.seats,
        license_id=args.license_id or "",
        expires_at=expires_at,
    )

    license_str = lm.create_license(payload, private_key, valid_for=valid_for)

    if args.out:
        Path(args.out).write_text(license_str + "\n", encoding="utf-8")
        print(f"license written to {args.out}", file=sys.stderr)
        # Echo metadata to stderr so stdout stays clean if redirected.
        print(f"license_id={payload.license_id} expires_at={payload.expires_at}",
              file=sys.stderr)
    else:
        print(license_str)
    return 0


# --------------------------------------------------------------------------- #
# Subcommand: verify
# --------------------------------------------------------------------------- #
def cmd_verify(args: argparse.Namespace) -> int:
    public_key = lm.load_public_key(read_key(args.key))
    license_str = read_license(args)
    at = parse_iso(args.at) if args.at else None

    result = lm.verify_license(license_str, public_key, at=at)

    if args.json:
        payload = result.payload.to_dict() if result.payload else None
        print(json.dumps(
            {"valid": result.valid, "reason": result.reason, "payload": payload},
            indent=2,
        ))
    else:
        status = "VALID" if result.valid else "INVALID"
        print(f"{status}: {result.reason}")
        if result.payload:
            p = result.payload
            print(f"  licensee   : {p.licensee}")
            print(f"  product    : {p.product}")
            print(f"  tier       : {p.tier}")
            print(f"  max_seats  : {p.max_seats}")
            print(f"  license_id : {p.license_id}")
            print(f"  issued_at  : {p.issued_at}")
            print(f"  expires_at : {p.expires_at or 'never (perpetual)'}")

    return 0 if result.valid else 1


# --------------------------------------------------------------------------- #
# Subcommand: inspect (decode only, NO signature check)
# --------------------------------------------------------------------------- #
def cmd_inspect(args: argparse.Namespace) -> int:
    license_str = read_license(args)
    try:
        payload_b64 = license_str.strip().split(".")[0]
        data = json.loads(lm._b64decode(payload_b64))
    except Exception as exc:
        sys.exit(f"error: cannot decode license payload: {exc}")

    print("WARNING: signature NOT verified — do not trust these values.",
          file=sys.stderr)
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0


# --------------------------------------------------------------------------- #
# Argument parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="licensecli",
        description="Issue and verify Ed25519-signed software licenses.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # keygen
    p_keygen = sub.add_parser("keygen", help="generate a vendor keypair")
    p_keygen.add_argument("--private", default="vendor_private.pem",
                          help="output path for the private key PEM (default: %(default)s)")
    p_keygen.add_argument("--public", default="vendor_public.pem",
                          help="output path for the public key PEM (default: %(default)s)")
    p_keygen.add_argument("--force", action="store_true",
                          help="overwrite existing key files")
    p_keygen.set_defaults(func=cmd_keygen)

    # issue
    p_issue = sub.add_parser("issue", help="sign and emit a license string")
    p_issue.add_argument("--key", required=True, metavar="PRIVATE_PEM",
                         help="path to the vendor private key PEM")
    p_issue.add_argument("--licensee", required=True, help="who the license is for")
    p_issue.add_argument("--product", required=True, help="product / SKU name")
    p_issue.add_argument("--tier", default="standard",
                         help="license tier (default: %(default)s)")
    p_issue.add_argument("--seats", type=int, default=1, metavar="N",
                         help="max seats (default: %(default)s)")
    group = p_issue.add_mutually_exclusive_group()
    group.add_argument("--valid-for", metavar="DURATION",
                       help="validity window, e.g. 365d, 52w, 24h, 90d12h")
    group.add_argument("--expires-at", metavar="ISO8601",
                       help="explicit expiry timestamp (overrides --valid-for)")
    p_issue.add_argument("--license-id", default="",
                         help="explicit license id (default: random UUID)")
    p_issue.add_argument("--out", metavar="PATH",
                         help="write license to file instead of stdout")
    p_issue.set_defaults(func=cmd_issue)

    # verify
    p_verify = sub.add_parser("verify", help="verify a license string")
    p_verify.add_argument("--key", required=True, metavar="PUBLIC_PEM",
                          help="path to the public key PEM")
    src = p_verify.add_mutually_exclusive_group()
    src.add_argument("--license", help="license string to verify")
    src.add_argument("--in", dest="in_path", metavar="PATH",
                     help="read license from file")
    p_verify.add_argument("--at", metavar="ISO8601",
                          help="check validity at this instant (default: now)")
    p_verify.add_argument("--json", action="store_true",
                          help="machine-readable JSON output")
    p_verify.set_defaults(func=cmd_verify)

    # inspect
    p_inspect = sub.add_parser("inspect",
                               help="decode payload WITHOUT verifying (debug)")
    src2 = p_inspect.add_mutually_exclusive_group()
    src2.add_argument("--license", help="license string to inspect")
    src2.add_argument("--in", dest="in_path", metavar="PATH",
                      help="read license from file")
    p_inspect.set_defaults(func=cmd_inspect)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        # Argument-shaped errors (bad duration, bad ISO date, etc.)
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
