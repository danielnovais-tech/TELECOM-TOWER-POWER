#!/bin/sh
set -u
echo "=== entrypoint.sh ==="
echo "PORT=${PORT:-8000}"
echo "DATABASE_URL is set: $([ -n "${DATABASE_URL:-}" ] && echo yes || echo no)"
echo "REDIS_URL is set: $([ -n "${REDIS_URL:-}" ] && echo yes || echo no)"

# Build React PWA if source exists but dist does not (non-Docker deploys)
if [ -d "frontend/src" ] && [ ! -d "frontend_dist" ]; then
    if command -v node >/dev/null 2>&1; then
        echo "Building React frontend..."
        (cd frontend && npm ci --ignore-scripts 2>/dev/null || npm install && npx vite build) \
            && cp -r frontend/dist frontend_dist \
            && echo "React frontend built → frontend_dist/" \
            || echo "WARN: React build failed, continuing without PWA frontend"
    else
        echo "WARN: Node.js not found, skipping React frontend build"
    fi
fi

# Run Alembic migrations with a hard timeout.
# If it hangs or fails, we still start the API.
echo "Running alembic upgrade head (60s timeout)..."
if command -v timeout >/dev/null 2>&1; then
    timeout 60 alembic upgrade head 2>&1 || echo "WARN: alembic failed or timed out (exit $?), continuing..."
else
    # Fallback: background alembic with manual kill
    alembic upgrade head 2>&1 &
    ALEMBIC_PID=$!
    SECONDS_WAITED=0
    while kill -0 "$ALEMBIC_PID" 2>/dev/null; do
        sleep 1
        SECONDS_WAITED=$((SECONDS_WAITED + 1))
        if [ "$SECONDS_WAITED" -ge 60 ]; then
            echo "WARN: alembic still running after 60s, killing..."
            kill -9 "$ALEMBIC_PID" 2>/dev/null || true
            break
        fi
    done
    wait "$ALEMBIC_PID" 2>/dev/null || echo "WARN: alembic exited with error (exit $?), continuing..."
fi
echo "Starting uvicorn..."
exec uvicorn telecom_tower_power_db:app --host 0.0.0.0 --port "${PORT:-8000}"
