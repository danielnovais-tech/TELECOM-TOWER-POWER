#!/bin/sh
set -e

API_PORT="${PORT:-8000}"
UI_PORT="${UI_PORT:-8501}"

# Run database migrations
echo "Running Alembic migrations..."
alembic upgrade head
echo "Migrations complete."

# Start FastAPI backend (async DB-backed)
uvicorn telecom_tower_power_db:app --host 0.0.0.0 --port "$API_PORT" &

# Wait for API to be ready
echo "Waiting for API on port $API_PORT..."
until python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$API_PORT/health')" 2>/dev/null; do
  sleep 1
done
echo "API ready."

# Pre-load tower data
python load_towers.py towers_brazil.csv "http://127.0.0.1:$API_PORT"

# Choose frontend: "hybrid" (default) = streamlit_app.py, "api" = frontend.py
FRONTEND_MODE="${FRONTEND_MODE:-hybrid}"
export API_URL="http://127.0.0.1:$API_PORT"
export API_BASE_URL="http://127.0.0.1:$API_PORT"

case "$FRONTEND_MODE" in
  api)
    echo "Starting API-client frontend (frontend.py)..."
    exec streamlit run frontend.py \
      --server.port "$UI_PORT" \
      --server.address 0.0.0.0 \
      --server.headless true
    ;;
  *)
    echo "Starting hybrid frontend (streamlit_app.py)..."
    exec streamlit run streamlit_app.py \
      --server.port "$UI_PORT" \
      --server.address 0.0.0.0 \
      --server.headless true
    ;;
esac
