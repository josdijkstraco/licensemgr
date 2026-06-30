# License Manager

A small, dependency-light software license manager built on **Ed25519 digital
signatures**. It lets you (the vendor) issue signed license keys, and lets your
application verify them **completely offline** — no license server, no network
call, no database lookup required.

It ships as two files:

| File             | Role                                                                 |
| ---------------- | ------------------------------------------------------------------- |
| `licensemgr.py`  | The library. Key generation, signing, verification. Import this.    |
| `licensecli.py`  | A command-line wrapper around the library for day-to-day use.       |

---

## How it works

```
            VENDOR (you)                         CUSTOMER'S MACHINE
   ┌──────────────────────────┐          ┌──────────────────────────────┐
   │  private key (SECRET)     │          │   your app + PUBLIC key       │
   │                           │          │                               │
   │  issue  ─────────────►  license string  ─────────────►  verify       │
   │  (signs payload)          │  e.g. emailed │   (checks signature       │
   │                           │  to customer  │    + expiry, offline)     │
   └──────────────────────────┘          └──────────────────────────────┘
```

- The **vendor** holds a *private key* and signs every license they issue.
- The **app** ships with the matching *public key* and verifies licenses locally.
- A valid signature proves two things:
  1. the license was issued by the holder of the private key (you), and
  2. its contents (expiry, tier, licensee, seats…) were **not modified**.

Because verification only needs the *public* key, it's safe to embed in your
shipped application — an attacker who extracts it still cannot forge or alter a
license.

### License format

A license is a single compact string, conceptually similar to a JWT but trimmed
down:

```
<base64url(payload_json)>.<base64url(signature)>
```

- The payload is canonical JSON (`sort_keys=True`, no whitespace), base64url-encoded.
- The signature is computed over the **base64url payload string** (the ASCII
  bytes left of the `.`), then base64url-encoded itself.
- base64url uses `-`/`_` and strips `=` padding, so the string is URL- and
  copy-paste-safe.

> Anyone can *read* a license payload (it's just base64). The signature is what
> makes it unforgeable. Never put secrets in a license payload.

---

## Requirements

- Python **3.9+** (works on 3.7+ thanks to `from __future__ import annotations`).
- The [`cryptography`](https://pypi.org/project/cryptography/) package — the only
  third-party dependency.

```bash
pip install cryptography
```

(Optionally drop both `.py` files into a virtualenv or your project tree; there
is no package install step.)

---

## Quick start (CLI)

```bash
# 0. Make the CLI executable (once)
chmod +x licensecli.py

# 1. Generate your vendor keypair. Keep the private key SECRET.
./licensecli.py keygen --private vendor_private.pem --public vendor_public.pem

# 2. Issue a license valid for one year, 50 seats, enterprise tier.
./licensecli.py issue \
    --key vendor_private.pem \
    --licensee "Acme Corp" \
    --product WidgetPro \
    --tier enterprise \
    --seats 50 \
    --valid-for 365d \
    --out acme.license

# 3. Verify it (this is what your app does, using the PUBLIC key).
./licensecli.py verify --key vendor_public.pem --in acme.license
```

Example verify output:

```
VALID: ok
  licensee   : Acme Corp
  product    : WidgetPro
  tier       : enterprise
  max_seats  : 50
  license_id : 5d8f0c2e-1a3b-4c7d-9e2f-aa1b2c3d4e5f
  issued_at  : 2026-06-30T12:00:00+00:00
  expires_at : 2027-06-30T12:00:00+00:00
```

---

## CLI reference

Run `./licensecli.py <command> --help` for full per-command help.

### `keygen` — create a vendor keypair

```bash
./licensecli.py keygen [--private PATH] [--public PATH] [--force]
```

| Flag        | Default               | Description                                   |
| ----------- | --------------------- | --------------------------------------------- |
| `--private` | `vendor_private.pem`  | Where to write the private key (PKCS#8 PEM).   |
| `--public`  | `vendor_public.pem`   | Where to write the public key (PEM).           |
| `--force`   | off                   | Overwrite key files if they already exist.     |

The private key is written with `chmod 600` (owner read/write only) on POSIX
filesystems. **Back it up somewhere safe and never commit it to version control.**
If you lose it you can no longer issue licenses that existing installs trust; if
it leaks, anyone can forge licenses and you must rotate keys (see
[Key rotation](#key-rotation)).

### `issue` — sign and emit a license (vendor side)

```bash
./licensecli.py issue --key PRIVATE_PEM --licensee NAME --product SKU \
    [--tier TIER] [--seats N] \
    [--valid-for DURATION | --expires-at ISO8601] \
    [--license-id ID] [--out PATH]
```

| Flag           | Required | Default      | Description                                          |
| -------------- | -------- | ------------ | ---------------------------------------------------- |
| `--key`        | ✅       | —            | Path to your **private** key PEM.                    |
| `--licensee`   | ✅       | —            | Who the license is for (name, company, email…).      |
| `--product`    | ✅       | —            | Product / SKU name.                                  |
| `--tier`       |          | `standard`   | Tier label, e.g. `standard`, `pro`, `enterprise`.    |
| `--seats`      |          | `1`          | Max seats (metadata — see note below).               |
| `--valid-for`  |          | —            | Validity window as a duration, e.g. `365d`.          |
| `--expires-at` |          | —            | Explicit expiry as ISO-8601. Overrides `--valid-for`.|
| `--license-id` |          | random UUID  | Set an explicit license id (else one is generated).  |
| `--out`        |          | stdout       | Write the license to a file instead of stdout.       |

`--valid-for` and `--expires-at` are mutually exclusive. Omit **both** to issue a
**perpetual** license (no expiry).

**Duration syntax** for `--valid-for` — concatenate `<number><unit>` segments:

| Unit | Meaning           | Examples                          |
| ---- | ----------------- | --------------------------------- |
| `s`  | seconds           | `30s`                             |
| `m`  | minutes           | `15m`                             |
| `h`  | hours             | `24h`                             |
| `d`  | days              | `365d`, `90d`                     |
| `w`  | weeks             | `52w`                             |
| `y`  | years (= 365 days)| `1y`, `2y`                        |

Segments combine: `90d12h`, `1y6w`, `24h30m`. Whitespace between segments is
tolerated.

When `--out` is used, the license string goes to the file and the
`license_id`/`expires_at` metadata is printed to **stderr**, so you can safely
pipe stdout elsewhere.

### `verify` — check a license (app side)

```bash
./licensecli.py verify --key PUBLIC_PEM
    [--license STRING | --in PATH] [--at ISO8601] [--json]
```

| Flag         | Description                                                          |
| ------------ | ------------------------------------------------------------------- |
| `--key`      | Path to the **public** key PEM (required).                          |
| `--license`  | License string passed directly on the command line.                 |
| `--in`       | Read the license from a file.                                        |
| `--at`       | Evaluate validity *as of* this instant (default: now). Great for tests.|
| `--json`     | Emit machine-readable JSON instead of the human table.              |

The license can come from three sources, checked in order: `--license`, then
`--in PATH`, then **stdin** if piped:

```bash
echo "<license-string>" | ./licensecli.py verify --key vendor_public.pem
```

`--at` lets you ask "would this license be valid on 2027-01-01?":

```bash
./licensecli.py verify --key vendor_public.pem --in acme.license \
    --at 2027-01-01T00:00:00Z --json
```

### `inspect` — decode without verifying (debug only)

```bash
./licensecli.py inspect [--license STRING | --in PATH]
```

Dumps the decoded payload JSON **without checking the signature**. It prints a
loud warning to stderr. Use this only for debugging — never trust its output for
access-control decisions.

### Exit codes

| Code | Meaning                                  |
| ---- | ---------------------------------------- |
| `0`  | success / license **valid**              |
| `1`  | failure / license **invalid**            |
| `2`  | usage error (bad arguments)              |

This makes the CLI easy to use in scripts:

```bash
if ./licensecli.py verify --key vendor_public.pem --in acme.license >/dev/null; then
    echo "licensed"
else
    echo "not licensed"
fi
```

---

## Integrating the library into your codebase

For a real application you'll usually call the library directly rather than
shelling out to the CLI. The whole surface lives in `licensemgr.py`.

### Public API

```python
import licensemgr as lm

# Key management
priv_pem, pub_pem = lm.generate_keypair()      # -> (bytes, bytes) PEM
private_key       = lm.load_private_key(priv_pem)   # vendor side
public_key        = lm.load_public_key(pub_pem)     # app side

# Payload (a dataclass)
payload = lm.LicensePayload(
    licensee="Acme Corp",
    product="WidgetPro",
    tier="enterprise",       # default "standard"
    max_seats=50,            # default 1
    license_id="",           # auto-filled with a UUID if empty
    issued_at="",            # auto-filled with now (UTC) if empty
    expires_at=None,         # None = perpetual
)

# Create (vendor side, needs the private key)
license_str = lm.create_license(payload, private_key, valid_for=timedelta(days=365))

# Verify (app side, needs only the public key)
result = lm.verify_license(license_str, public_key)   # -> VerificationResult
if result:                       # VerificationResult is truthy when valid
    print("valid for", result.payload.licensee)
else:
    print("rejected:", result.reason)
```

`verify_license` returns a `VerificationResult` with:

- `result.valid` — `bool`
- `result.reason` — human-readable string (`"ok"`, `"license expired"`,
  `"invalid signature (tampered or wrong key)"`, …)
- `result.payload` — the decoded `LicensePayload` (present even on most failures,
  so you can show *which* license expired). **Only trust it when `result.valid`
  is `True`.**
- `bool(result)` is `result.valid`, so `if result:` works directly.

### `LicensePayload` fields

| Field         | Type            | Default      | Notes                                            |
| ------------- | --------------- | ------------ | ------------------------------------------------ |
| `licensee`    | `str`           | (required)   | Who the license is for.                          |
| `product`     | `str`           | (required)   | Product / SKU. Verify this matches *your* app.   |
| `tier`        | `str`           | `"standard"` | Drives feature gating in your app.               |
| `license_id`  | `str`           | auto UUID    | Unique id; useful for support & revocation lists.|
| `issued_at`   | `str` (ISO-8601)| auto now-UTC | Used for the not-yet-valid check.                |
| `expires_at`  | `str` or `None` | `None`       | `None` = perpetual.                              |
| `max_seats`   | `int`           | `1`          | Metadata only — your app enforces it.            |

### What `verify_license` checks (and what it doesn't)

It **does** check, in this order:

1. **Structure** — the string is `<payload>.<signature>`.
2. **Signature** — the security-critical step. Fails on any tampering or wrong key.
3. **Payload decode** — valid base64 + JSON.
4. **Time window** —
   - `issued_at` is not in the future (with a 5-minute clock-skew allowance), and
   - `expires_at`, if present, is not in the past.

It does **not** enforce:

- **Seat counts.** `max_seats` is delivered to you as trusted metadata; *you*
  count concurrent users/devices and act on it.
- **Revocation.** Verification is fully offline, so there's no built-in "this
  license was refunded" check. See [Revocation](#revocation) below for how to add it.
- **Product matching.** Always confirm `payload.product == "YourProduct"`
  yourself — otherwise a valid license for a *different* product would pass.

### A drop-in integration example

Embed the public key in your app (a bundled `.pem` file, or a string constant)
and gate startup on verification:

```python
# app_licensing.py
from pathlib import Path
import licensemgr as lm

# Option A: ship the public key as a file alongside your app.
PUBLIC_KEY = lm.load_public_key(
    (Path(__file__).parent / "vendor_public.pem").read_bytes()
)

# Option B: paste it inline so there's no external file to remove/replace.
# PUBLIC_KEY = lm.load_public_key(b"""-----BEGIN PUBLIC KEY-----
# MCowBQYDK2VwAyEA...your key...
# -----END PUBLIC KEY-----
# """)

PRODUCT = "WidgetPro"


class LicenseError(Exception):
    pass


def load_and_check(license_str: str) -> lm.LicensePayload:
    """Verify a license string and return its payload, or raise LicenseError."""
    result = lm.verify_license(license_str, PUBLIC_KEY)
    if not result:
        raise LicenseError(f"License rejected: {result.reason}")

    payload = result.payload
    if payload.product != PRODUCT:
        raise LicenseError(
            f"License is for {payload.product!r}, not {PRODUCT!r}"
        )
    return payload


def feature_enabled(payload: lm.LicensePayload, feature: str) -> bool:
    """Example tier-based feature gating."""
    by_tier = {
        "standard":   {"core"},
        "pro":        {"core", "reports"},
        "enterprise": {"core", "reports", "sso", "audit_log"},
    }
    return feature in by_tier.get(payload.tier, set())


if __name__ == "__main__":
    text = Path("license.key").read_text().strip()
    try:
        lic = load_and_check(text)
    except LicenseError as e:
        raise SystemExit(f"❌ {e}")
    print(f"✅ Licensed to {lic.licensee} ({lic.tier}, {lic.max_seats} seats)")
    print("SSO enabled:", feature_enabled(lic, "sso"))
```

Where to get the license string at runtime is up to you — common patterns:
read a `~/.config/yourapp/license.key` file, an env var, a paste field in the
UI, or a value in your config store.

---

## Operational guidance

### Distributing keys

- **Private key**: keep it offline / in a secrets manager. It only ever lives on
  the machine where you *issue* licenses. Never bundle it with the app.
- **Public key**: ship it with the app. It cannot be used to forge licenses, so
  it's fine for it to be extracted. (A determined attacker can still *patch out*
  your verification call — signatures stop forgery, not binary patching. For most
  products that's an acceptable trade-off; harden the binary separately if needed.)

### Revocation

Because verification is offline, a freshly issued license is valid until it
expires. To support revocation:

- Prefer **short-lived licenses** (e.g. `--valid-for 30d`) and re-issue on
  renewal. This bounds the damage window without any infrastructure.
- Or maintain a **revocation list** of `license_id`s your app checks (shipped in
  updates, or fetched if your app has network access). The `license_id` field
  exists precisely for this.

### Key rotation

If your private key leaks (or you simply want to roll it):

1. `keygen` a new keypair.
2. Ship an app update bundling the **new** public key (optionally keep the old
   public key too, so existing licenses keep working during a transition).
3. Issue all new licenses with the new private key.
4. Once the old licenses age out, drop the old public key.

### Clock considerations

Expiry and not-yet-valid checks rely on the verifying machine's clock. A user who
sets their clock back can extend an expired license; a 5-minute skew allowance
exists for the *issued_at* check. If your threat model includes clock tampering,
combine licensing with an occasional trusted-time check.

---

## Troubleshooting

| Symptom                                                   | Likely cause / fix                                                        |
| --------------------------------------------------------- | ------------------------------------------------------------------------- |
| `could not import licensemgr.py`                          | Keep `licensemgr.py` next to `licensecli.py`; `pip install cryptography`.  |
| `INVALID: invalid signature (tampered or wrong key)`      | Wrong public key for this license, or the license string was modified.     |
| `INVALID: license expired`                                | Past `expires_at`. Re-issue, or test with `--at` for an earlier date.      |
| `INVALID: license not yet valid`                          | `issued_at` is in the future — usually a clock mismatch between machines.  |
| `INVALID: malformed: expected '<payload>.<signature>'`    | The license string was truncated or whitespace-mangled in transit.        |
| `error: invalid duration ...`                             | Use forms like `365d`, `52w`, `24h`, `90d12h` (see duration table).        |

---

## At a glance

```bash
# Vendor: set up once
./licensecli.py keygen --private vendor_private.pem --public vendor_public.pem

# Vendor: issue per customer
./licensecli.py issue --key vendor_private.pem \
    --licensee "Acme Corp" --product WidgetPro \
    --tier pro --seats 10 --valid-for 1y --out acme.license

# App: verify (exit 0 = valid, 1 = invalid)
./licensecli.py verify --key vendor_public.pem --in acme.license
```

Library, in three lines:

```python
import licensemgr as lm
result = lm.verify_license(open("license.key").read().strip(),
                           lm.load_public_key(open("vendor_public.pem","rb").read()))
print(result.reason, result.payload and result.payload.licensee)
```
# licensemgr
