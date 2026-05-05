# syntax=docker/dockerfile:1.7
# Image is suitable for BOTH:
#   * SaaS deployment (default) — uses Amazon Bedrock + S3 + KMS via env vars.
#   * Self-hosted Enterprise (docker-compose.onprem.yml) — set
#     LLM_PROVIDER=ollama, S3_ENDPOINT_URL=http://minio:9000, leave
#     AUDIT_KMS_KEY_ID empty. No code changes required; backends are
#     selected at runtime by llm_provider.py and boto3 endpoint env vars.
# ── Stage 1: Build React frontend ──────────────────────────
FROM node:22-alpine AS frontend-build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --ignore-scripts 2>/dev/null || npm install
COPY frontend/ .
RUN npx vite build

# ── Stage 2: Python application ────────────────────────────
# Pinned to 3.11: tflite-runtime has no 3.13 wheels yet (ai-edge-litert).
# Revisit once upstream ships 3.13 support.
FROM python:3.11-slim

ENV CONDA_DIR=/opt/conda
ENV PATH=$CONDA_DIR/bin:$PATH

# Prevent Python from buffering stdout/stderr (important for container logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --create-home appuser

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        wget git ca-certificates \
        libcairo2 libpango-1.0-0 libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 libffi8 shared-mime-info \
        fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

RUN wget -qO /tmp/miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh && \
    bash /tmp/miniconda.sh -b -p "$CONDA_DIR" && \
    rm -f /tmp/miniconda.sh

RUN conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main && \
    conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r && \
    conda install -y python=3.11 gdal numpy && \
    git clone https://github.com/edwardoughton/itmlogic.git /opt/itmlogic && \
    pip install vcs_versioning && \
    cd /opt/itmlogic && python setup.py install && \
    conda clean -afy

WORKDIR /app

# Install dependencies first (layer caching via Docker's normal layer cache).
# Note: BuildKit --mount=type=cache is avoided because Railpack rejects it
# without its internal cacheKey prefix on the id.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    # The ITU-R P.1812 digital maps (P1812.npz) are downloaded at boot
    # by entrypoint.sh into the Py1812 install dir. Make that dir
    # writable by the non-root runtime user.
    if python -c "import Py1812" 2>/dev/null; then \
        chown -R 1000:1000 "$(python -c 'import os, Py1812; print(os.path.dirname(Py1812.__file__))')"; \
    fi

# Copy application code
COPY telecom_tower_power_db.py .
COPY telecom_tower_power_api.py .
COPY telecom_tower_power.py .
COPY worker.py .
COPY s3_storage.py .
COPY pdf_generator.py .
COPY tier1_pdf_reports.py .
COPY srtm_elevation.py .
COPY srtm_prefetch.py .
COPY bedrock_service.py .
COPY llm_provider.py .
COPY coverage_predict.py .
COPY coverage_model.npz .
COPY itu_p1812.py .
COPY observation_store.py .
COPY stripe_billing.py .
COPY stripe_webhook_service.py .
COPY offline_mode.py .
COPY tower_db.py .
COPY job_store.py .
COPY repeater_jobs_store.py .
COPY hop_cache.py .
COPY audit_log.py .
COPY sso_auth.py .
COPY graphql_schema.py .
COPY rf_engines_router.py .
COPY rf_engines/ rf_engines/
COPY interference_engine.py .
COPY sqs_lambda_worker.py .
COPY batch_gpu_interference_worker.py .
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

# Create runtime writable directories and fix permissions.
RUN mkdir -p srtm_data job_results && \
    chmod +x start.sh entrypoint.sh load_secrets.sh && \
    chown -R appuser:appuser /app

USER appuser

# PORT is set by Railway at runtime; default 8000 for local use
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen('http://localhost:'+os.environ.get('PORT','8000')+'/health')"

# Default: API only. Use start.sh for full-stack (API + UI).
CMD ["./entrypoint.sh"]
