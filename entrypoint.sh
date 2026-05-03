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

# MapBiomas LULC raster (clutter feature). Optional. When
# MAPBIOMAS_RASTER_S3_URI is set we mirror the GeoTIFF onto local disk
# so the lazy rasterio.open() inside the API process is fast.
# Footprint: a Brazil-wide Collection 9 raster is ~3-5 GB; allow a long
# timeout (5 min) and skip the download if a local file already exists
# and is newer than 7 days.
if [ -n "${MAPBIOMAS_RASTER_S3_URI:-}" ] && [ -n "${MAPBIOMAS_RASTER_PATH:-}" ]; then
    _MB_DIR=$(dirname "${MAPBIOMAS_RASTER_PATH}")
    mkdir -p "$_MB_DIR" || true
    _MB_NEEDS_DL=1
    if [ -f "${MAPBIOMAS_RASTER_PATH}" ]; then
        # Skip if file is < 7 days old.
        if find "${MAPBIOMAS_RASTER_PATH}" -mtime -7 2>/dev/null | grep -q .; then
            echo "MapBiomas raster present and < 7 days old, skipping download"
            _MB_NEEDS_DL=0
        fi
    fi
    if [ "$_MB_NEEDS_DL" -eq 1 ]; then
        echo "Downloading MapBiomas raster ${MAPBIOMAS_RASTER_S3_URI} → ${MAPBIOMAS_RASTER_PATH} (5min timeout)..."
        _MB_DL_CMD='import os, sys, boto3
from urllib.parse import urlparse
uri = os.environ["MAPBIOMAS_RASTER_S3_URI"]
dst = os.environ["MAPBIOMAS_RASTER_PATH"]
u = urlparse(uri)
if u.scheme != "s3" or not u.netloc or not u.path:
    print(f"ERROR: invalid MAPBIOMAS_RASTER_S3_URI: {uri}", file=sys.stderr); sys.exit(2)
bucket, key = u.netloc, u.path.lstrip("/")
boto3.client("s3").download_file(bucket, key, dst)
print(f"MapBiomas raster ready ({os.path.getsize(dst)} bytes)")'
        if command -v timeout >/dev/null 2>&1; then
            timeout 300 python -c "$_MB_DL_CMD" \
                || echo "WARN: MapBiomas raster download failed, clutter feature disabled"
        else
            python -c "$_MB_DL_CMD" \
                || echo "WARN: MapBiomas raster download failed, clutter feature disabled"
        fi
        unset _MB_DL_CMD
    fi
    unset _MB_DIR _MB_NEEDS_DL
fi

# ITU-R P.1812 digital maps (Py1812). Optional. The maps file
# ``P1812.npz`` is derived offline from N050.TXT + DN50.TXT (ITU
# digital products, redistribution forbidden). We host the derived
# .npz in a private S3 bucket and copy it into the Py1812 install
# directory so ``Py1812.P1812.bt_loss`` works without re-running
# initiate_digital_maps.py inside the container.
# Skipped silently when the env var is unset; on failure the API
# still serves predictions via the ridge model.
if [ -n "${ITU_P1812_NPZ_S3_URI:-}" ]; then
    _P1812_DL_CMD='import os, sys, boto3
from urllib.parse import urlparse
try:
    import Py1812
except Exception as exc:
    print(f"INFO: Py1812 not installed ({exc}); skipping P.1812 maps", flush=True)
    sys.exit(0)
uri = os.environ["ITU_P1812_NPZ_S3_URI"]
u = urlparse(uri)
if u.scheme != "s3" or not u.netloc or not u.path:
    print(f"ERROR: invalid ITU_P1812_NPZ_S3_URI: {uri}", file=sys.stderr); sys.exit(2)
bucket, key = u.netloc, u.path.lstrip("/")
dst = os.path.join(os.path.dirname(Py1812.__file__), "P1812.npz")
boto3.client("s3").download_file(bucket, key, dst)
print(f"ITU-R P.1812 digital maps ready at {dst} ({os.path.getsize(dst)} bytes)")'
    if command -v timeout >/dev/null 2>&1; then
        timeout 120 python -c "$_P1812_DL_CMD" \
            || echo "WARN: ITU P.1812 maps download failed, hybrid model disabled"
    else
        python -c "$_P1812_DL_CMD" \
            || echo "WARN: ITU P.1812 maps download failed, hybrid model disabled"
    fi
    unset _P1812_DL_CMD
fi

# Sionna learned-propagation engine artefact (TFLite + JSON sidecar).
# Optional: the registry simply skips the engine when the files are
# absent. The trained model is published by retrain-sionna.yml under
#   $S3/current/sionna_model.tflite
#   $S3/current/sionna_features.json
# We download both into $SIONNA_MODEL_DIR (default /srv/models/) and
# only flip SIONNA_DISABLED=0 *after* both files land — half-loaded
# state would make the engine refuse to start anyway, but flipping
# the gate explicitly here keeps the boot log honest.
if [ -n "${SIONNA_MODEL_S3_URI:-}" ] && [ -n "${SIONNA_FEATURES_S3_URI:-}" ]; then
    _SIONNA_DIR="${SIONNA_MODEL_DIR:-/srv/models}"
    mkdir -p "$_SIONNA_DIR" || true
    : "${SIONNA_MODEL_PATH:=$_SIONNA_DIR/sionna_model.tflite}"
    : "${SIONNA_FEATURES_PATH:=$_SIONNA_DIR/sionna_features.json}"
    export SIONNA_MODEL_PATH SIONNA_FEATURES_PATH
    echo "Fetching Sionna artefact from S3 (60s timeout)..."
    _SIONNA_DL_CMD='import os, sys, boto3
from urllib.parse import urlparse
def _dl(env_uri, dst):
    uri = os.environ[env_uri]
    u = urlparse(uri)
    if u.scheme != "s3" or not u.netloc or not u.path:
        print(f"ERROR: invalid {env_uri}: {uri}", file=sys.stderr); sys.exit(2)
    boto3.client("s3").download_file(u.netloc, u.path.lstrip("/"), dst)
    print(f"{env_uri} → {dst} ({os.path.getsize(dst)} bytes)")
_dl("SIONNA_MODEL_S3_URI",    os.environ["SIONNA_MODEL_PATH"])
_dl("SIONNA_FEATURES_S3_URI", os.environ["SIONNA_FEATURES_PATH"])'
    if command -v timeout >/dev/null 2>&1; then
        timeout 60 python -c "$_SIONNA_DL_CMD" \
            && export SIONNA_DISABLED=0 \
            && echo "Sionna engine enabled" \
            || echo "WARN: Sionna artefact download failed, engine stays disabled"
    else
        python -c "$_SIONNA_DL_CMD" \
            && export SIONNA_DISABLED=0 \
            && echo "Sionna engine enabled" \
            || echo "WARN: Sionna artefact download failed, engine stays disabled"
    fi
    unset _SIONNA_DL_CMD _SIONNA_DIR
fi

# rfsignals-cli (clean-room Rust empirical-models binary). Optional.
# When RF_SIGNALS_S3_URL is set, fetch the binary into a writable path
# and export RF_SIGNALS_BIN so the Python adapter resolves it. The
# binary is published as public-read by the rf-signals-publish CI
# workflow, so no AWS creds are required at boot. SHA-256 verification
# happens when RF_SIGNALS_S3_SHA256 is also set (recommended for prod).
if [ -n "${RF_SIGNALS_S3_URL:-}" ]; then
    _RFS_DIR="${RF_SIGNALS_BIN_DIR:-/opt/rfsignals}"
    _RFS_BIN="$_RFS_DIR/rfsignals-cli"
    mkdir -p "$_RFS_DIR" 2>/dev/null || true
    if command -v curl >/dev/null 2>&1; then
        echo "Fetching rfsignals-cli from ${RF_SIGNALS_S3_URL} (60s timeout)..."
        if curl -fsSL --max-time 60 -o "$_RFS_BIN.tmp" "$RF_SIGNALS_S3_URL"; then
            if [ -n "${RF_SIGNALS_S3_SHA256:-}" ] && command -v sha256sum >/dev/null 2>&1; then
                _got=$(sha256sum "$_RFS_BIN.tmp" | awk '{print $1}')
                if [ "$_got" != "$RF_SIGNALS_S3_SHA256" ]; then
                    echo "WARN: rfsignals-cli sha256 mismatch (got=$_got expected=$RF_SIGNALS_S3_SHA256), discarding"
                    rm -f "$_RFS_BIN.tmp"
                    _RFS_BIN=""
                fi
                unset _got
            fi
            if [ -n "${_RFS_BIN:-}" ] && [ -f "$_RFS_BIN.tmp" ]; then
                chmod 0755 "$_RFS_BIN.tmp"
                mv -f "$_RFS_BIN.tmp" "$_RFS_BIN"
                export RF_SIGNALS_BIN="$_RFS_BIN"
                echo "rfsignals-cli ready: $RF_SIGNALS_BIN ($(stat -c%s "$_RFS_BIN" 2>/dev/null || echo "?") bytes)"
            fi
        else
            echo "WARN: rfsignals-cli download failed, engine stays unavailable"
            rm -f "$_RFS_BIN.tmp" 2>/dev/null || true
        fi
    else
        echo "WARN: curl not available in image, cannot fetch rfsignals-cli"
    fi
    unset _RFS_DIR _RFS_BIN
fi

if [ "${SERVICE_TYPE:-}" = "webhook" ]; then
    echo "SERVICE_TYPE=webhook → starting stripe_webhook_service"
    exec uvicorn stripe_webhook_service:app --host 0.0.0.0 --port "${PORT:-8080}"
else
    exec uvicorn telecom_tower_power_api:app --host 0.0.0.0 --port "${PORT:-8000}"
fi
