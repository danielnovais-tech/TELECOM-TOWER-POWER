web: ./entrypoint.sh
streamlit: ./start.sh
worker: rq worker batch_pdfs --url ${REDIS_URL:-redis://localhost:6379}
