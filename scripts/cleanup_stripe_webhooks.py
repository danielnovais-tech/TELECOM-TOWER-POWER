#!/usr/bin/env python3
"""Delete stale Stripe webhook endpoints, keeping only the canonical one.

Canonical (kept):
    https://api.telecomtowerpower.com.br/stripe_webhook

Stale (deleted) — every one of these fires duplicate events:
    - https://ec2.telecomtowerpower.com.br/stripe/webhook
    - https://stripe-webhook-production-484c.up.railway.app/stripe_webhook
    - https://telecom-tower-power-api.onrender.com/stripe/webhook

Usage:
    export STRIPE_SECRET_KEY=sk_live_...        # or pass --key
    python scripts/cleanup_stripe_webhooks.py            # dry-run (default)
    python scripts/cleanup_stripe_webhooks.py --apply    # actually delete

The script is idempotent: re-running after a successful apply is a no-op.
"""
from __future__ import annotations
import argparse
import os
import sys
from urllib.parse import urlparse

try:
    import stripe
except ImportError:
    sys.stderr.write("Install stripe: pip install stripe\n")
    sys.exit(2)


CANONICAL_URL = "https://api.telecomtowerpower.com.br/stripe_webhook"

# Match by host + path so http/https or trailing-slash variants don't slip through.
STALE_HOSTS = {
    ("ec2.telecomtowerpower.com.br", "/stripe/webhook"),
    ("stripe-webhook-production-484c.up.railway.app", "/stripe_webhook"),
    ("telecom-tower-power-api.onrender.com", "/stripe/webhook"),
}


def _norm(url: str) -> tuple[str, str]:
    p = urlparse(url)
    return (p.netloc.lower(), p.path.rstrip("/") or "/")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", default=os.environ.get("STRIPE_SECRET_KEY", ""),
                    help="Stripe secret key (defaults to $STRIPE_SECRET_KEY).")
    ap.add_argument("--apply", action="store_true",
                    help="Actually delete. Without this flag, runs in dry-run mode.")
    args = ap.parse_args()

    if not args.key:
        sys.stderr.write("ERROR: STRIPE_SECRET_KEY not set and --key not provided.\n")
        return 2
    if not args.key.startswith(("sk_live_", "sk_test_")):
        sys.stderr.write("ERROR: key does not look like a Stripe secret key.\n")
        return 2

    stripe.api_key = args.key
    mode = "LIVE" if args.key.startswith("sk_live_") else "TEST"
    action = "DELETE" if args.apply else "DRY-RUN"
    print(f"Mode: {mode}  Action: {action}")
    print(f"Canonical (will be kept): {CANONICAL_URL}\n")

    deleted = kept = unknown = 0
    canonical_norm = _norm(CANONICAL_URL)

    # WebhookEndpoint.list paginates; auto_paging_iter handles it.
    for ep in stripe.WebhookEndpoint.list(limit=100).auto_paging_iter():
        url = ep.get("url", "")
        norm = _norm(url)
        livemode = ep.get("livemode", False)
        ep_id = ep.get("id", "")

        if norm == canonical_norm:
            print(f"  KEEP    {ep_id}  livemode={livemode}  {url}")
            kept += 1
            continue

        if norm in STALE_HOSTS:
            if args.apply:
                stripe.WebhookEndpoint.delete(ep_id)
                print(f"  DELETED {ep_id}  livemode={livemode}  {url}")
            else:
                print(f"  WOULD DELETE {ep_id}  livemode={livemode}  {url}")
            deleted += 1
            continue

        print(f"  ?? UNKNOWN {ep_id}  livemode={livemode}  {url}  (left untouched — review manually)")
        unknown += 1

    print(f"\nSummary: kept={kept}  {'deleted' if args.apply else 'to-delete'}={deleted}  unknown={unknown}")
    if not args.apply and deleted:
        print("Re-run with --apply to actually delete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
