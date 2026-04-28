# Web API (main FastAPI service)
web: ./entrypoint.sh

# Background job worker (RQ).
# `set -u` aborts immediately with a clear message if REDIS_URL is unset,
# instead of letting `rq worker --url` swallow an empty argument and emit a
# confusing traceback minutes later.
worker: sh -c 'set -eu; exec rq worker batch_pdfs --url "$REDIS_URL"'

# Stripe webhook handler is NOT a separate Procfile process.
# It is activated by setting SERVICE_TYPE=webhook on the stripe-webhook
# Railway service, which causes entrypoint.sh to start stripe_webhook_service
# instead of the main API.
