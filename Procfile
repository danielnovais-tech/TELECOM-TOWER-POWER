# Web API (main FastAPI service)
web: ./entrypoint.sh

# Background job worker (RQ)
worker: rq worker batch_pdfs --url ${REDIS_URL}

# Stripe webhook handler is NOT a separate Procfile process.
# It is activated by setting SERVICE_TYPE=webhook on the stripe-webhook
# Railway service, which causes entrypoint.sh to start stripe_webhook_service
# instead of the main API.
