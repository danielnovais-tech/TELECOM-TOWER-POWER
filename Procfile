web: ./entrypoint.sh
worker: rq worker batch_pdfs --url ${REDIS_URL:-redis://localhost:6379}
