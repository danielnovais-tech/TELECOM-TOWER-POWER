"""
stripe_billing.py
Self-service signup, Stripe Checkout, and API-key lifecycle.

Environment variables (all required for live mode):
  STRIPE_SECRET_KEY      – sk_test_... or sk_live_...
  STRIPE_WEBHOOK_SECRET  – whsec_...
  STRIPE_PRICE_PRO       – price_... (monthly Pro plan)
  STRIPE_PRICE_ENTERPRISE– price_... (monthly Enterprise plan)
  FRONTEND_URL           – e.g. http://localhost:3000  (for Checkout redirects)
"""

import hashlib
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Dict, Optional

import stripe

logger = logging.getLogger("stripe_billing")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_PRO = os.getenv("STRIPE_PRICE_PRO", "")
STRIPE_PRICE_ENTERPRISE = os.getenv("STRIPE_PRICE_ENTERPRISE", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

stripe.api_key = STRIPE_SECRET_KEY

# Map tier name → Stripe Price ID
TIER_PRICE_MAP: Dict[str, str] = {
    "pro": STRIPE_PRICE_PRO,
    "enterprise": STRIPE_PRICE_ENTERPRISE,
}

# ---------------------------------------------------------------------------
# Persistent JSON key-store  (swap for a real DB in production)
# ---------------------------------------------------------------------------
_STORE_PATH = Path(os.getenv("KEY_STORE_PATH", "./key_store.json"))


def _load_store() -> Dict:
    if _STORE_PATH.exists():
        return json.loads(_STORE_PATH.read_text())
    return {}


def _save_store(data: Dict) -> None:
    _STORE_PATH.write_text(json.dumps(data, indent=2))


def _generate_api_key() -> str:
    """Generate a secure random API key prefixed with `ttp_`."""
    return "ttp_" + secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Public helpers consumed by telecom_tower_power_api.py
# ---------------------------------------------------------------------------

def get_all_keys() -> Dict[str, Dict]:
    """Return the full key→metadata mapping (for the auth layer)."""
    return _load_store()


def lookup_key(api_key: str) -> Optional[Dict]:
    """Return metadata for a single key, or None."""
    return _load_store().get(api_key)


def register_free_user(email: str) -> Dict:
    """
    Instant self-service signup for the free tier.
    Returns {"api_key": ..., "tier": "free", "email": ...}.
    """
    store = _load_store()

    # prevent duplicate sign-ups for the same e-mail
    for meta in store.values():
        if meta.get("email") == email:
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
    store[key] = record
    _save_store(store)
    logger.info("Registered free user %s", email)
    return {"api_key": key, **record}


# ---------------------------------------------------------------------------
# Stripe Checkout
# ---------------------------------------------------------------------------

def create_checkout_session(email: str, tier: str, country: Optional[str] = None) -> str:
    """
    Create a Stripe Checkout Session for a paid tier.
    Returns the Checkout Session URL the frontend should redirect to.

    *country* (ISO 3166-1 alpha-2) is optional; when provided for an
    enterprise plan, SRTM tiles for that country will be pre-downloaded
    after payment.
    """
    if not STRIPE_SECRET_KEY:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured")

    price_id = TIER_PRICE_MAP.get(tier)
    if not price_id:
        raise ValueError(f"No Stripe price configured for tier '{tier}'")

    metadata = {"tier": tier, "email": email}
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
        store[existing_key]["stripe_customer_id"] = customer_id
        store[existing_key]["stripe_subscription_id"] = subscription_id
        _save_store(store)
        logger.info("Upgraded %s to %s (key %s…)", email, tier, existing_key[:12])
        _maybe_prefetch_srtm(tier, session)
        return {"action": "upgraded", "email": email, "tier": tier}

    # Otherwise create a new key
    key = _generate_api_key()
    store[key] = {
        "tier": tier,
        "owner": email,
        "email": email,
        "stripe_customer_id": customer_id,
        "stripe_subscription_id": subscription_id,
        "created": time.time(),
    }
    _save_store(store)
    logger.info("Provisioned %s key for %s", tier, email)
    _maybe_prefetch_srtm(tier, session)
    return {"action": "provisioned", "email": email, "tier": tier}


def _on_subscription_deleted(subscription: dict) -> Dict:
    """Downgrade the user back to free when their subscription is cancelled."""
    sub_id = subscription["id"]
    store = _load_store()

    for key, meta in store.items():
        if meta.get("stripe_subscription_id") == sub_id:
            meta["tier"] = "free"
            meta["stripe_subscription_id"] = None
            _save_store(store)
            logger.info("Downgraded %s to free (subscription cancelled)", meta["email"])
            return {"action": "downgraded", "email": meta["email"]}

    return {"action": "no_match", "subscription_id": sub_id}


def get_key_for_email(email: str) -> Optional[str]:
    """Look up the API key for a given email address."""
    store = _load_store()
    for key, meta in store.items():
        if meta.get("email") == email:
            return key
    return None


def get_key_info_for_email(email: str) -> Optional[Dict]:
    """Return the API key and its metadata for a given email, or None."""
    store = _load_store()
    for key, meta in store.items():
        if meta.get("email") == email:
            return {"api_key": key, **meta}
    return None


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
