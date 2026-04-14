"""
stripe_webhook_service.py – Standalone lightweight Stripe webhook handler.

Deploy this as a separate service/container so that webhook processing
does not share fate with the main API.  It uses the same stripe_billing
module and key-store, so provisioning logic stays in one place.

Run:
    uvicorn stripe_webhook_service:app --port 8001

Environment variables (same as the main API):
    STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, KEY_STORE_PATH
"""

import logging
import os

from fastapi import FastAPI, HTTPException, Request
from pythonjsonlogger import jsonlogger

import stripe_billing

# ── Logging ──────────────────────────────────────────────────────
_handler = logging.StreamHandler()
_handler.setFormatter(
    jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
)
logger = logging.getLogger("stripe_webhook_service")
logger.addHandler(_handler)
logger.setLevel(logging.INFO)

# ── App ──────────────────────────────────────────────────────────
app = FastAPI(
    title="Stripe Webhook Service",
    description="Isolated webhook handler for Stripe events",
    version="1.0.0",
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/stripe/webhook")
@app.post("/stripe_webhook")
async def stripe_webhook(request: Request):
    """
    Receive Stripe webhook events (checkout.session.completed,
    customer.subscription.deleted, etc.).
    """
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        result = stripe_billing.handle_webhook_event(payload, sig)
    except stripe_billing.stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return result


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "stripe_webhook_service:app",
        host="0.0.0.0",
        port=int(os.getenv("WEBHOOK_PORT", "8001")),
        log_level="info",
    )
