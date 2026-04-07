web: alembic upgrade head && uvicorn telecom_tower_power_db:app --host 0.0.0.0 --port ${PORT:-8000}
worker: rq worker batch_pdfs --url ${REDIS_URL:-redis://localhost:6379}
