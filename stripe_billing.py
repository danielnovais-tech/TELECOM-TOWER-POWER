"""
stripe_billing.py
Self-service signup, Stripe Checkout, and API-key lifecycle.

Environment variables (all required for live mode):
  STRIPE_SECRET_KEY      – sk_test_... or sk_live_...
  STRIPE_WEBHOOK_SECRET  – whsec_...
  STRIPE_PRICE_PRO       – price_... (monthly Pro plan)
  STRIPE_PRICE_ENTERPRISE– price_... (monthly Enterprise plan)
  FRONTEND_URL           – e.g. http://localhost:3000  (for Checkout redirects)

SES email (optional – sends welcome email with API key after checkout):
  SES_SMTP_HOST          – e.g. email-smtp.sa-east-1.amazonaws.com
  SES_SMTP_PORT          – 587 (STARTTLS)
  SES_SMTP_USERNAME      – SMTP username from SES console
  SES_SMTP_PASSWORD      – SMTP password from SES console
  SES_FROM_ADDRESS       – Verified sender, e.g. no-reply@telecomtowerpower.com.br
"""

import hashlib
import json
import logging
import os
import secrets
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, Optional

import stripe

import key_store_db

logger = logging.getLogger("stripe_billing")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
# Monthly prices
STRIPE_PRICE_STARTER = os.getenv("STRIPE_PRICE_STARTER", "")
STRIPE_PRICE_PRO = os.getenv("STRIPE_PRICE_PRO", "")
STRIPE_PRICE_BUSINESS = os.getenv("STRIPE_PRICE_BUSINESS", "")
STRIPE_PRICE_ENTERPRISE = os.getenv("STRIPE_PRICE_ENTERPRISE", "")
# Annual prices (typically 15-20% discount over 12 × monthly)
STRIPE_PRICE_STARTER_ANNUAL = os.getenv("STRIPE_PRICE_STARTER_ANNUAL", "")
STRIPE_PRICE_PRO_ANNUAL = os.getenv("STRIPE_PRICE_PRO_ANNUAL", "")
STRIPE_PRICE_BUSINESS_ANNUAL = os.getenv("STRIPE_PRICE_BUSINESS_ANNUAL", "")
STRIPE_PRICE_ENTERPRISE_ANNUAL = os.getenv("STRIPE_PRICE_ENTERPRISE_ANNUAL", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://app.telecomtowerpower.com.br")

stripe.api_key = STRIPE_SECRET_KEY

# SES SMTP settings (for welcome emails)
_SES_HOST = os.getenv("SES_SMTP_HOST", "email-smtp.sa-east-1.amazonaws.com")
_SES_PORT = int(os.getenv("SES_SMTP_PORT", "587"))
_SES_USER = os.getenv("SES_SMTP_USERNAME", "")
_SES_PASS = os.getenv("SES_SMTP_PASSWORD", "")
_SES_FROM = os.getenv("SES_FROM_ADDRESS", "no-reply@telecomtowerpower.com.br")

# Map tier name → Stripe Price ID. Entries with empty values are ignored
# by create_checkout_session (raises ValueError) so a tier remains safely
# unavailable until its STRIPE_PRICE_* env var is set.
TIER_PRICE_MAP: Dict[str, str] = {
    "starter": STRIPE_PRICE_STARTER,
    "pro": STRIPE_PRICE_PRO,
    "business": STRIPE_PRICE_BUSINESS,
    "enterprise": STRIPE_PRICE_ENTERPRISE,
}

# Annual prices — keyed the same way, used when billing_cycle="annual".
TIER_PRICE_MAP_ANNUAL: Dict[str, str] = {
    "starter": STRIPE_PRICE_STARTER_ANNUAL,
    "pro": STRIPE_PRICE_PRO_ANNUAL,
    "business": STRIPE_PRICE_BUSINESS_ANNUAL,
    "enterprise": STRIPE_PRICE_ENTERPRISE_ANNUAL,
}

# ---------------------------------------------------------------------------
# Persistent key-store
# ---------------------------------------------------------------------------
# Backend is selected by ``key_store_db`` (PostgreSQL when DATABASE_URL is
# set, JSON file fallback otherwise).  KEY_STORE_PATH is still honoured by
# the JSON backend for local dev compatibility.
_STORE_PATH = Path(os.getenv("KEY_STORE_PATH", "./key_store.json"))


def _load_store() -> Dict:
    return key_store_db.get_all_keys()


def _save_store(data: Dict) -> None:
    """Reconcile the in-memory snapshot with the backend.

    The legacy code path called this with a *full* dict snapshot after every
    mutation. With the DB backend we translate that into per-key upserts and
    deletes for keys removed from the snapshot.
    """
    backend = key_store_db.get_backend()
    current = backend.get_all_keys()
    for k, rec in data.items():
        if current.get(k) != rec:
            backend.upsert_key(k, rec)
    for k in current.keys() - data.keys():
        backend.delete_key(k)


def _generate_api_key() -> str:
    """Generate a secure random API key prefixed with `ttp_`."""
    return "ttp_" + secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Public helpers consumed by telecom_tower_power_api.py
# ---------------------------------------------------------------------------

def get_all_keys() -> Dict[str, Dict]:
    """Return the full key→metadata mapping (for the auth layer)."""
    return key_store_db.get_all_keys()


def lookup_key(api_key: str) -> Optional[Dict]:
    """Return metadata for a single key, or None."""
    return key_store_db.lookup_key(api_key)


def register_free_user(email: str) -> Dict:
    """
    Instant self-service signup for the free tier.
    Returns {"api_key": ..., "tier": "free", "email": ...}.
    """
    store = _load_store()

    existing_emails = [m.get("email") for m in store.values()]
    logger.info(
        "register_free_user: email=%s store_id=%s keys=%d emails=%s",
        email, id(store), len(store), existing_emails,
    )

    # prevent duplicate sign-ups for the same e-mail
    for meta in store.values():
        if meta.get("email") == email:
            logger.info("Duplicate detected for %s", email)
            raise ValueError("An API key already exists for this email")

    key = _generate_api_key()
    record = {
        "tier": "free",
        "owner": email,
        "email": email,
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
        "created": time.time(),
    }
    key_store_db.upsert_key(key, record)
    logger.info("Registered free user %s", email)
    return {"api_key": key, **record}


# ---------------------------------------------------------------------------
# Stripe Checkout
# ---------------------------------------------------------------------------

def create_checkout_session(email: str, tier: str, country: Optional[str] = None, billing_cycle: str = "monthly") -> str:
    """
    Create a Stripe Checkout Session for a paid tier.
    Returns the Checkout Session URL the frontend should redirect to.

    *billing_cycle* is "monthly" (default) or "annual"; the annual variant
    maps to STRIPE_PRICE_*_ANNUAL env vars which should be configured with
    a 15-20% discount over 12 × the monthly price.
    *country* (ISO 3166-1 alpha-2) is optional; when provided for an
    enterprise plan, SRTM tiles for that country will be pre-downloaded
    after payment.
    """
    if not STRIPE_SECRET_KEY:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured")

    price_map = TIER_PRICE_MAP_ANNUAL if billing_cycle == "annual" else TIER_PRICE_MAP
    price_id = price_map.get(tier)
    if not price_id:
        raise ValueError(f"No Stripe price configured for tier '{tier}' ({billing_cycle})")

    metadata = {"tier": tier, "email": email, "billing_cycle": billing_cycle}
    if country:
        metadata["country"] = country.upper()

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer_email=email,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{FRONTEND_URL}/signup/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{FRONTEND_URL}/signup/cancel",
        metadata=metadata,
    )
    return session.url


# ---------------------------------------------------------------------------
# Welcome email via Amazon SES
# ---------------------------------------------------------------------------

def send_welcome_email(email: str, api_key: str, tier: str) -> bool:
    """
    Send a welcome email with the API key after successful checkout.
    Returns True on success, False on failure (non-fatal).
    """
    if not _SES_USER or not _SES_PASS:
        logger.warning("SES credentials not configured; skipping welcome email for %s", email)
        return False

    tier_label = tier.capitalize()
    docs_url = "https://api.telecomtowerpower.com.br/docs"

    subject = f"Welcome to Telecom Tower Power – Your {tier_label} API Key"

    text_body = (
        f"Welcome to Telecom Tower Power!\n\n"
        f"Your {tier_label} plan is now active.\n\n"
        f"API Key: {api_key}\n\n"
        f"Quick start:\n"
        f"  curl -H 'X-API-Key: {api_key}' "
        f"https://api.telecomtowerpower.com.br/towers/nearby?lat=-15.79&lon=-47.88\n\n"
        f"Full API docs: {docs_url}\n\n"
        f"Keep your key confidential. If compromised, contact support.\n\n"
        f"— Telecom Tower Power Team"
    )

    html_body = f"""\
<div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px">
  <h2 style="color:#1a73e8">Welcome to Telecom Tower Power!</h2>
  <p>Your <strong>{tier_label}</strong> plan is now active.</p>
  <div style="background:#f4f4f4;padding:16px;border-radius:8px;margin:16px 0">
    <p style="margin:0 0 4px"><strong>Your API Key:</strong></p>
    <code style="font-size:14px;word-break:break-all">{api_key}</code>
  </div>
  <p><strong>Quick start:</strong></p>
  <pre style="background:#272822;color:#f8f8f2;padding:12px;border-radius:6px;overflow-x:auto">
curl -H 'X-API-Key: {api_key}' \\
  https://api.telecomtowerpower.com.br/towers/nearby?lat=-15.79&amp;lon=-47.88</pre>
  <p><a href="{docs_url}">Full API documentation &rarr;</a></p>
  <hr style="border:none;border-top:1px solid #ddd;margin:24px 0">
  <p style="font-size:12px;color:#888">
    Keep your key confidential. If compromised, contact support immediately.
  </p>
</div>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = _SES_FROM
    msg["To"] = email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(_SES_HOST, _SES_PORT) as server:
            server.starttls()
            server.login(_SES_USER, _SES_PASS)
            server.sendmail(_SES_FROM, [email], msg.as_string())
        logger.info("Welcome email sent to %s (%s tier)", email, tier)
        return True
    except Exception:
        logger.exception("Failed to send welcome email to %s", email)
        return False


# ---------------------------------------------------------------------------
# SRTM tile pre-download for enterprise accounts
# ---------------------------------------------------------------------------

def _maybe_prefetch_srtm(tier: str, session: dict) -> None:
    """If this is an enterprise checkout with a country, start background prefetch."""
    if tier != "enterprise":
        return
    country = session.get("metadata", {}).get("country")
    if not country:
        return
    try:
        from srtm_prefetch import prefetch_country_async, COUNTRY_BOUNDS
        if country.upper() not in COUNTRY_BOUNDS:
            logger.warning("No SRTM bounds for country %s; skipping prefetch", country)
            return
        prefetch_country_async(country)
    except Exception:
        logger.exception("Failed to start SRTM prefetch for %s", country)


# ---------------------------------------------------------------------------
# Stripe Webhook handler
# ---------------------------------------------------------------------------

def handle_webhook_event(payload: bytes, sig_header: str) -> Dict:
    """
    Verify and process a Stripe webhook event.
    Returns a dict with the action taken.
    """
    if not STRIPE_WEBHOOK_SECRET:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET is not configured")

    event = stripe.Webhook.construct_event(
        payload, sig_header, STRIPE_WEBHOOK_SECRET
    )

    if event["type"] == "checkout.session.completed":
        return _on_checkout_completed(event["data"]["object"])

    if event["type"] == "customer.subscription.deleted":
        return _on_subscription_deleted(event["data"]["object"])

    return {"action": "ignored", "type": event["type"]}


def _on_checkout_completed(session: dict) -> Dict:
    """Provision a paid API key after successful checkout."""
    email = session["metadata"]["email"]
    tier = session["metadata"]["tier"]
    billing_cycle = session["metadata"].get("billing_cycle", "monthly")
    customer_id = session["customer"]
    subscription_id = session["subscription"]

    store = _load_store()

    # Upgrade existing free key if present
    existing_key = None
    for k, meta in store.items():
        if meta.get("email") == email:
            existing_key = k
            break

    if existing_key:
        store[existing_key]["tier"] = tier
        store[existing_key]["billing_cycle"] = billing_cycle
        store[existing_key]["stripe_customer_id"] = customer_id
        store[existing_key]["stripe_subscription_id"] = subscription_id
        key_store_db.upsert_key(existing_key, store[existing_key])
        logger.info("Upgraded %s to %s/%s (key %s…)", email, tier, billing_cycle, existing_key[:12])
        _maybe_prefetch_srtm(tier, session)
        send_welcome_email(email, existing_key, tier)
        return {"action": "upgraded", "email": email, "tier": tier, "billing_cycle": billing_cycle}

    # Otherwise create a new key
    key = _generate_api_key()
    record = {
        "tier": tier,
        "billing_cycle": billing_cycle,
        "owner": email,
        "email": email,
        "stripe_customer_id": customer_id,
        "stripe_subscription_id": subscription_id,
        "created": time.time(),
    }
    key_store_db.upsert_key(key, record)
    logger.info("Provisioned %s/%s key for %s", tier, billing_cycle, email)
    _maybe_prefetch_srtm(tier, session)
    send_welcome_email(email, key, tier)
    return {"action": "provisioned", "email": email, "tier": tier, "billing_cycle": billing_cycle}


def _on_subscription_deleted(subscription: dict) -> Dict:
    """Downgrade the user back to free when their subscription is cancelled."""
    sub_id = subscription["id"]
    store = _load_store()

    for key, meta in store.items():
        if meta.get("stripe_subscription_id") == sub_id:
            meta["tier"] = "free"
            meta["stripe_subscription_id"] = None
            key_store_db.upsert_key(key, meta)
            logger.info("Downgraded %s to free (subscription cancelled)", meta["email"])
            return {"action": "downgraded", "email": meta["email"]}

    return {"action": "no_match", "subscription_id": sub_id}


def get_key_for_email(email: str) -> Optional[str]:
    """Look up the API key for a given email address."""
    return key_store_db.get_key_for_email(email)


def get_key_info_for_email(email: str) -> Optional[Dict]:
    """Return the API key and its metadata for a given email, or None."""
    return key_store_db.get_record_by_email(email)


def retrieve_key_from_checkout_session(session_id: str) -> Dict:
    """
    Given a Stripe Checkout Session ID, retrieve the customer's email
    and return their API key info.  Raises ValueError if session is
    unpaid or no key exists yet (webhook may not have fired).
    """
    if not STRIPE_SECRET_KEY:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured")

    session = stripe.checkout.Session.retrieve(session_id)

    if session.payment_status != "paid":
        raise ValueError("Payment not completed")

    email = session.get("customer_email") or session["metadata"].get("email")
    if not email:
        raise ValueError("No email associated with this checkout session")

    info = get_key_info_for_email(email)
    if info is None:
        raise ValueError(
            "API key not yet provisioned — the webhook may still be processing. "
            "Please retry in a few seconds."
        )
    return info
