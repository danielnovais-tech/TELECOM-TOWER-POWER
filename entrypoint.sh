#!/bin/sh
set -u

# Lock down .env if present (owner-read/write only)
if [ -f .env ]; then
    chmod 600 .env 2>/dev/null || true
fi

# ── Load Docker Compose secrets from /run/secrets/ ────────────
# Secrets mounted as files never appear in `docker inspect`.
# We bridge them into env vars so the Python app's os.getenv() works unchanged.
_SECRETS_LOADED=""
for _sf in /run/secrets/*; do
    [ -f "$_sf" ] || continue
    _name=$(basename "$_sf" | tr '[:lower:]-' '[:upper:]_')
    # Only set if not already provided via -e / environment:
    if eval "[ -z \"\${${_name}:-}\" ]"; then
        _val=$(cat "$_sf")
        export "$_name"="$_val"
        _SECRETS_LOADED="${_SECRETS_LOADED} ${_name}"
    fi
done
unset _sf _name _val

echo "=== entrypoint.sh ==="
echo "PORT=${PORT:-8000}"
echo "DATABASE_URL is set: $([ -n "${DATABASE_URL:-}" ] && echo yes || echo no)"
echo "REDIS_URL is set: $([ -n "${REDIS_URL:-}" ] && echo yes || echo no)"
echo "Secrets loaded from files:${_SECRETS_LOADED:- (none)}"

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

# One-shot, idempotent backfill of legacy key_store.json into Postgres.
# Safe to re-run on every boot: upserts only, no destructive ops. Skips when
# DATABASE_URL is unset (local dev) or when key_store.json is absent.
if [ -n "${DATABASE_URL:-}" ] && [ -f "${KEY_STORE_PATH:-key_store.json}" ]; then
    echo "Running key_store backfill (idempotent, 30s timeout)..."
    if command -v timeout >/dev/null 2>&1; then
        timeout 30 python migrate_keystore_to_db.py 2>&1 \
            || echo "WARN: key_store backfill failed or timed out (exit $?), continuing..."
    else
        python migrate_keystore_to_db.py 2>&1 \
            || echo "WARN: key_store backfill exit $?, continuing..."
    fi
fi

echo "Starting uvicorn..."
# Pull the latest coverage_model.npz from S3 if COVERAGE_MODEL_S3_URI is set.
# Best-effort: any failure (no creds, network, missing key) falls back to the
# baked-in artefact. Hard-capped at 30s so a slow/wedged S3 call cannot block
# the API from coming up. On success, log the loaded model's metadata so we
# can verify in container logs which artefact (S3 vs baked) is active.
if [ -n "${COVERAGE_MODEL_S3_URI:-}" ]; then
    echo "Refreshing coverage model from ${COVERAGE_MODEL_S3_URI} (30s timeout)..."
    _COV_REFRESH_CMD='import sys, coverage_predict as c
ok = c.refresh_from_s3()
if ok:
    m = c.get_model(refresh=True)
    if m is not None:
        print(f"Coverage model active: version={m.version} rmse_db={m.rmse_db:.4f} n_train={m.n_train}")
sys.exit(0 if ok else 1)'
    if command -v timeout >/dev/null 2>&1; then
        timeout 30 python -c "$_COV_REFRESH_CMD"
    else
        python -c "$_COV_REFRESH_CMD"
    fi
    _COV_RC=$?
    if [ "$_COV_RC" -eq 0 ]; then
        echo "Coverage model refreshed from S3"
    else
        echo "WARN: coverage model S3 refresh failed (exit $_COV_RC), using baked artefact"
    fi
    unset _COV_REFRESH_CMD _COV_RC
fi

# Sync per-band ridge artefacts (700/850/.../3500 MHz) when configured.
# Independent of the global model above so a partial S3 setup is OK:
# missing band files just fall back to the global ridge.
if [ -n "${COVERAGE_BAND_MODELS_S3_PREFIX:-}" ] && [ -n "${COVERAGE_BAND_MODEL_DIR:-}" ]; then
    echo "Syncing per-band coverage models from ${COVERAGE_BAND_MODELS_S3_PREFIX} (30s timeout)..."
    _BAND_REFRESH_CMD='import sys, coverage_predict as c
ok = c.refresh_band_models_from_s3()
ba = c.get_band_model(refresh=True)
if ba is not None:
    print(f"Band-aware coverage model active: {len(ba.models)} bands ({sorted(ba.models)})")
sys.exit(0 if ok else 1)'
    if command -v timeout >/dev/null 2>&1; then
        timeout 30 python -c "$_BAND_REFRESH_CMD" || echo "WARN: band model sync failed (exit $?), continuing with global ridge"
    else
        python -c "$_BAND_REFRESH_CMD" || echo "WARN: band model sync failed (exit $?), continuing with global ridge"
    fi
    unset _BAND_REFRESH_CMD
fi

if [ "${SERVICE_TYPE:-}" = "webhook" ]; then
    echo "SERVICE_TYPE=webhook → starting stripe_webhook_service"
    exec uvicorn stripe_webhook_service:app --host 0.0.0.0 --port "${PORT:-8080}"
else
    exec uvicorn telecom_tower_power_api:app --host 0.0.0.0 --port "${PORT:-8000}"
fi
