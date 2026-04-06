FROM python:3.10-slim

# Prevent Python from buffering stdout/stderr (important for container logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --create-home appuser

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY telecom_tower_power_api.py .
COPY telecom_tower_power.py .
COPY pdf_generator.py .
COPY srtm_elevation.py .
COPY stripe_billing.py .
COPY tower_db.py .
COPY job_store.py .
COPY batch_worker.py .
COPY migrate_csv_to_db.py .
COPY models.py .
COPY alembic.ini .
COPY migrations/ migrations/
COPY frontend.py .
COPY load_towers.py .
COPY towers_brazil.csv .
COPY sample_receivers.csv .
COPY sample_batch_test.csv .
COPY start.sh .

# Create srtm_data directory and fix permissions
RUN mkdir -p srtm_data job_results && chmod +x start.sh && chown -R appuser:appuser /app

USER appuser

# PORT is set by Railway/Render at runtime; default 8000 for local use
ENV PORT=8000
EXPOSE ${PORT}

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["sh", "-c", "python -c \"import urllib.request,os; urllib.request.urlopen('http://localhost:'+os.environ.get('PORT','8000')+'/health')\""]

# Default: API only. Use start.sh for full-stack (API + UI).
CMD ["sh", "-c", "uvicorn telecom_tower_power_api:app --host 0.0.0.0 --port ${PORT}"]
