#!/usr/bin/env python3
"""Generate a TOTP seed + otpauth:// URI for an admin email.

Output goes to stdout in three forms:
  1. The raw base32 secret (paste into ADMIN_TOTP_SECRETS comma list).
  2. The otpauth:// URI (paste into a 2FA app, or feed into a QR generator).
  3. An ASCII QR code, if `qrcode` is importable; otherwise a hint.

Examples:
  ./scripts/admin_totp_enroll.py daniel@example.com
  ./scripts/admin_totp_enroll.py --issuer "TTP Admin" daniel@example.com

Then update the secret bundle:

  EXISTING=$(gh secret list --json name -q '.[].name' | grep -x ADMIN_TOTP_SECRETS || true)
  # Compose the comma-separated list of email:secret entries and rotate:
  printf 'admin1@x.com:BASE32A,admin2@x.com:BASE32B' | gh secret set ADMIN_TOTP_SECRETS
  gh workflow run update-ec2-admin-totp-secrets.yml -f acknowledge_rotation=ROTATE
"""
from __future__ import annotations

import argparse
import base64
import secrets
import sys
from urllib.parse import quote


def gen_secret(num_bytes: int = 20) -> str:
    """Return a base32 (no padding) TOTP secret. 20 bytes = 160 bits, RFC 6238 default."""
    raw = secrets.token_bytes(num_bytes)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def otpauth_uri(secret: str, account: str, issuer: str) -> str:
    label = quote(f"{issuer}:{account}")
    params = (
        f"secret={secret}"
        f"&issuer={quote(issuer)}"
        "&algorithm=SHA1"
        "&digits=6"
        "&period=30"
    )
    return f"otpauth://totp/{label}?{params}"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("email", help="Admin email (used as TOTP label and matched against admin_key in /admin routes).")
    ap.add_argument("--issuer", default="TELECOM-TOWER-POWER", help="Issuer name shown in the authenticator app.")
    ap.add_argument("--bytes", type=int, default=20, help="Secret entropy in bytes (default 20 = 160 bits).")
    args = ap.parse_args(argv)

    if args.bytes < 16:
        print("refusing to generate <128-bit secret", file=sys.stderr)
        return 2

    secret = gen_secret(args.bytes)
    uri = otpauth_uri(secret, args.email, args.issuer)

    print(f"email:   {args.email}")
    print(f"secret:  {secret}")
    print(f"entry:   {args.email}:{secret}")
    print()
    print(f"otpauth: {uri}")
    print()

    try:
        import qrcode  # type: ignore

        qr = qrcode.QRCode(border=1)
        qr.add_data(uri)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except ImportError:
        print("(qrcode lib not installed; render the otpauth:// URI with any QR generator)")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
