# syntax=docker/dockerfile:1.7
# ── Stage 1: Build React frontend ──────────────────────────
FROM node:22-alpine AS frontend-build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --ignore-scripts 2>/dev/null || npm install
COPY frontend/ .
RUN npx vite build

# ── Stage 2: Python application ────────────────────────────
FROM python:3.13-slim

# Prevent Python from buffering stdout/stderr (important for container logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --create-home appuser

WORKDIR /app

# Install dependencies first (layer caching via Docker's normal layer cache).
# Note: BuildKit --mount=type=cache is avoided because Railpack rejects it
# without its internal cacheKey prefix on the id.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY telecom_tower_power_db.py .
COPY telecom_tower_power_api.py .
COPY telecom_tower_power.py .
COPY worker.py .
COPY s3_storage.py .
COPY pdf_generator.py .
COPY srtm_elevation.py .
COPY srtm_prefetch.py .
COPY bedrock_service.py .
COPY coverage_predict.py .
COPY coverage_model.npz .
COPY observation_store.py .
COPY stripe_billing.py .
COPY stripe_webhook_service.py .
COPY tower_db.py .
COPY job_store.py .
COPY repeater_jobs_store.py .
COPY hop_cache.py .
COPY audit_log.py .
COPY sso_auth.py .
COPY graphql_schema.py .
COPY coverage_export.py .
COPY batch_worker.py .
COPY tracing.py .
COPY migrate_csv_to_db.py .
COPY models.py .
COPY alembic.ini .
COPY migrations/ migrations/
COPY frontend.py .
COPY api_client.py .
COPY streamlit_app.py .
COPY load_towers.py .
COPY load_opencellid.py .
COPY load_brazil_towers.py .
COPY geocoder_br.py .
COPY towers_brazil.csv .
COPY sample_receivers.csv .
COPY sample_batch_test.csv .
COPY key_store.json .
COPY key_store_db.py .
COPY migrate_keystore_to_db.py .
COPY start.sh .
COPY entrypoint.sh .
COPY load_secrets.sh .

# Operational scripts (e.g. audit_log_prune.py for the daily retention job).
COPY scripts/audit_log_prune.py scripts/audit_log_prune.py
COPY scripts/audit_log_encrypt.py scripts/audit_log_encrypt.py

# Copy built React frontend
COPY --from=frontend-build /app/dist frontend_dist/

# Create srtm_data directory and fix permissions
RUN mkdir -p srtm_data job_results && chmod +x start.sh entrypoint.sh load_secrets.sh && chown -R appuser:appuser /app

USER appuser

# PORT is set by Railway at runtime; default 8000 for local use
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen('http://localhost:'+os.environ.get('PORT','8000')+'/health')"

# Default: API only. Use start.sh for full-stack (API + UI).
CMD ["./entrypoint.sh"]
