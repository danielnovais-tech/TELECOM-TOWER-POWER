#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Telecom Tower Power — Environment Secrets Setup
# Sets secrets on: Render (API), Railway (CLI), AWS (SSM Parameter Store)
# ============================================================================

# ── Secret Values ────────────────────────────────────────────────────────────
# Replace these with real values in production
#
# Tier requirements:
#   Free tier:         VALID_API_KEYS (+ DATABASE_URL, REDIS_URL auto-provided)
#   Pro/Enterprise:    All of the above + S3_*, STRIPE_*, AWS_*
#
# Note: DATABASE_URL and REDIS_URL are auto-injected by platform add-ons
#       (Render PostgreSQL, Railway Postgres plugin, etc.) — do NOT set manually.

# ── Required for ALL tiers ─────────────────────────────────────────
VALID_API_KEYS='{"demo-key-free-001":"free","demo-key-pro-001":"pro","demo-key-enterprise-001":"enterprise"}'

# ── Required for Pro/Enterprise only ───────────────────────────────
STRIPE_SECRET_KEY="8571"
STRIPE_WEBHOOK_SECRET="8571"
STRIPE_PRICE_PRO="8571"
STRIPE_PRICE_ENTERPRISE="8571"
AWS_ACCESS_KEY_ID="8571"
AWS_SECRET_ACCESS_KEY="8571"
S3_BUCKET_NAME="8571"
S3_REGION="8571"

# ── App config ───────────────────────────────────────────────────
FRONTEND_URL="https://telecom-tower-power-ui.onrender.com"
CORS_ORIGINS="https://telecom-tower-power-ui.onrender.com,https://app.telecomtowerpower.com"
MAX_BATCH_ROWS="500"

# ── Render Service IDs ───────────────────────────────────────────────────────
# Find your service IDs at: https://dashboard.render.com → select service → Settings → ID
RENDER_API_SERVICE_ID="srv-d78n5qtm5p6s73epli50"       # telecom-tower-power-api
RENDER_WORKER_SERVICE_ID="${RENDER_WORKER_SERVICE_ID:-}" # telecom-tower-power-rq-worker (set if you have one)
RENDER_UI_SERVICE_ID="srv-d78rss14tr6s73cfkdu0"         # telecom-tower-power-ui (Streamlit)

# ── Render API Key (get from https://dashboard.render.com/account/api-keys) ──
RENDER_API_KEY="${RENDER_API_KEY:-}"

# ── AWS SSM prefix ───────────────────────────────────────────────────────────
SSM_PREFIX="/telecom-tower-power/prod"

# ============================================================================
# RENDER — Set env vars via REST API
# ============================================================================
set_render_secrets() {
    if [[ -z "$RENDER_API_KEY" ]]; then
        echo "✗ RENDER_API_KEY not set."
        echo ""
        echo "  To get your API key:"
        echo "    1. Go to https://dashboard.render.com/account/api-keys"
        echo "    2. Click 'Create API Key'"
        echo "    3. Copy the key (starts with rnd_...)"
        echo ""
        echo "  Then run:"
        echo "    export RENDER_API_KEY=rnd_xxxxxxxxxxxx"
        echo "    $0 render"
        return 1
    fi

    local had_errors=0

    # ── API Service ──────────────────────────────────────────
    echo "▶ Setting Render env vars on API service ($RENDER_API_SERVICE_ID)..."

    local api_response
    local api_http_code
    api_response=$(curl -sS -w "\n%{http_code}" -X PUT \
        "https://api.render.com/v1/services/${RENDER_API_SERVICE_ID}/env-vars" \
        -H "Authorization: Bearer ${RENDER_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$(cat <<EOF
[
  {"key": "STRIPE_SECRET_KEY",      "value": "${STRIPE_SECRET_KEY}"},
  {"key": "STRIPE_WEBHOOK_SECRET",  "value": "${STRIPE_WEBHOOK_SECRET}"},
  {"key": "STRIPE_PRICE_PRO",       "value": "${STRIPE_PRICE_PRO}"},
  {"key": "STRIPE_PRICE_ENTERPRISE","value": "${STRIPE_PRICE_ENTERPRISE}"},
  {"key": "AWS_ACCESS_KEY_ID",      "value": "${AWS_ACCESS_KEY_ID}"},
  {"key": "AWS_SECRET_ACCESS_KEY",  "value": "${AWS_SECRET_ACCESS_KEY}"},
  {"key": "S3_BUCKET_NAME",         "value": "${S3_BUCKET_NAME}"},
  {"key": "S3_REGION",              "value": "${S3_REGION}"},
  {"key": "FRONTEND_URL",           "value": "${FRONTEND_URL}"},
  {"key": "CORS_ORIGINS",           "value": "${CORS_ORIGINS}"},
  {"key": "MAX_BATCH_ROWS",         "value": "${MAX_BATCH_ROWS}"},
  {"key": "VALID_API_KEYS",         "value": ${VALID_API_KEYS}}
]
EOF
)")

    api_http_code=$(echo "$api_response" | tail -1)
    local api_body
    api_body=$(echo "$api_response" | sed '$d')

    if [[ "$api_http_code" -ge 200 && "$api_http_code" -lt 300 ]]; then
        echo "  ✓ API service: ${api_http_code} OK"
    else
        echo "  ✗ API service FAILED (HTTP ${api_http_code}):"
        echo "$api_body" | python3 -m json.tool 2>/dev/null || echo "    $api_body"
        if [[ "$api_http_code" == "401" ]]; then
            echo ""
            echo "  → Unauthorized. Your RENDER_API_KEY is invalid or expired."
            echo "    Get a new one at: https://dashboard.render.com/account/api-keys"
        elif [[ "$api_http_code" == "404" ]]; then
            echo ""
            echo "  → Service not found. Check RENDER_API_SERVICE_ID ($RENDER_API_SERVICE_ID)"
        fi
        had_errors=1
    fi

    echo ""

    # ── Worker Service (RQ worker needs S3 creds) ────────────
    if [[ -z "$RENDER_WORKER_SERVICE_ID" ]]; then
        echo "⏭  Skipping worker service (RENDER_WORKER_SERVICE_ID not set)"
    else
        echo "▶ Setting Render env vars on worker service ($RENDER_WORKER_SERVICE_ID)..."

    local worker_response
    local worker_http_code
    worker_response=$(curl -sS -w "\n%{http_code}" -X PUT \
        "https://api.render.com/v1/services/${RENDER_WORKER_SERVICE_ID}/env-vars" \
        -H "Authorization: Bearer ${RENDER_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$(cat <<EOF
[
  {"key": "AWS_ACCESS_KEY_ID",      "value": "${AWS_ACCESS_KEY_ID}"},
  {"key": "AWS_SECRET_ACCESS_KEY",  "value": "${AWS_SECRET_ACCESS_KEY}"},
  {"key": "S3_BUCKET_NAME",         "value": "${S3_BUCKET_NAME}"},
  {"key": "S3_REGION",              "value": "${S3_REGION}"}
]
EOF
)")

    worker_http_code=$(echo "$worker_response" | tail -1)
    local worker_body
    worker_body=$(echo "$worker_response" | sed '$d')

    if [[ "$worker_http_code" -ge 200 && "$worker_http_code" -lt 300 ]]; then
        echo "  ✓ Worker service: ${worker_http_code} OK"
    else
        echo "  ✗ Worker service FAILED (HTTP ${worker_http_code}):"
        echo "$worker_body" | python3 -m json.tool 2>/dev/null || echo "    $worker_body"
        had_errors=1
    fi
    fi  # end RENDER_WORKER_SERVICE_ID check

    echo ""
    if [[ $had_errors -eq 0 ]]; then
        echo "✓ Render secrets set successfully. Redeploy services to pick up changes."
    else
        echo "✗ Render secrets had errors — see above. Fix authentication and retry."
        return 1
    fi
}

# ============================================================================
# RAILWAY — Set env vars via CLI
# ============================================================================
set_railway_secrets() {
    if ! command -v railway &>/dev/null; then
        echo "✗ Railway CLI not found. Install: curl -fsSL https://railway.com/install.sh | sh"
        return 1
    fi

    # Check if logged in
    if ! railway whoami &>/dev/null 2>&1; then
        echo "▶ Railway login required..."
        railway login
    fi

    echo "▶ Setting Railway environment variables..."
    echo "  (Make sure you have linked to the correct project: railway link)"

    railway variables set \
        VALID_API_KEYS="$VALID_API_KEYS" \
        STRIPE_SECRET_KEY="$STRIPE_SECRET_KEY" \
        STRIPE_WEBHOOK_SECRET="$STRIPE_WEBHOOK_SECRET" \
        STRIPE_PRICE_PRO="$STRIPE_PRICE_PRO" \
        STRIPE_PRICE_ENTERPRISE="$STRIPE_PRICE_ENTERPRISE" \
        AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
        AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
        S3_BUCKET_NAME="$S3_BUCKET_NAME" \
        S3_REGION="$S3_REGION" \
        FRONTEND_URL="$FRONTEND_URL" \
        CORS_ORIGINS="$CORS_ORIGINS" \
        MAX_BATCH_ROWS="$MAX_BATCH_ROWS"

    echo ""
    echo "  Note: DATABASE_URL and REDIS_URL are auto-provided by Railway plugins."
    echo "  Do NOT set them manually."

    echo "✓ Railway variables set. They will apply on next deploy."
    echo ""
    echo "  Verify with: railway variables"
}

# ============================================================================
# AWS — Store secrets in SSM Parameter Store (SecureString)
# ============================================================================
set_aws_secrets() {
    if ! command -v aws &>/dev/null; then
        echo "✗ AWS CLI not found."
        return 1
    fi

    # Check credentials
    if ! aws sts get-caller-identity &>/dev/null 2>&1; then
        echo "▶ AWS credentials not configured. Running: aws configure"
        aws configure
    fi

    echo "▶ Storing secrets in AWS SSM Parameter Store (${SSM_PREFIX}/*)..."

    # Note: DATABASE_URL and REDIS_URL are NOT stored here — they are
    # auto-provided by managed services (RDS, ElastiCache, etc.)
    declare -A secrets=(
        ["VALID_API_KEYS"]="$VALID_API_KEYS"
        ["STRIPE_SECRET_KEY"]="$STRIPE_SECRET_KEY"
        ["STRIPE_WEBHOOK_SECRET"]="$STRIPE_WEBHOOK_SECRET"
        ["STRIPE_PRICE_PRO"]="$STRIPE_PRICE_PRO"
        ["STRIPE_PRICE_ENTERPRISE"]="$STRIPE_PRICE_ENTERPRISE"
        ["AWS_ACCESS_KEY_ID"]="$AWS_ACCESS_KEY_ID"
        ["AWS_SECRET_ACCESS_KEY"]="$AWS_SECRET_ACCESS_KEY"
        ["S3_BUCKET_NAME"]="$S3_BUCKET_NAME"
        ["S3_REGION"]="$S3_REGION"
        ["FRONTEND_URL"]="$FRONTEND_URL"
        ["CORS_ORIGINS"]="$CORS_ORIGINS"
    )

    for key in "${!secrets[@]}"; do
        value="${secrets[$key]}"
        if [[ -z "$value" ]]; then
            echo "  ⏭  Skipping $key (empty)"
            continue
        fi
        echo "  ▸ ${SSM_PREFIX}/${key}"
        aws ssm put-parameter \
            --name "${SSM_PREFIX}/${key}" \
            --value "$value" \
            --type SecureString \
            --overwrite \
            --no-cli-pager \
            --output text --query 'Version' | xargs -I{} echo "    → version {}"
    done

    echo ""
    echo "✓ AWS SSM parameters stored under ${SSM_PREFIX}/"
    echo "  Verify with: aws ssm get-parameters-by-path --path ${SSM_PREFIX}/ --with-decryption"
}

# ============================================================================
# MAIN — Run one or all platforms
# ============================================================================
usage() {
    echo "Usage: $0 [render|railway|aws|all]"
    echo ""
    echo "  render   — Set env vars on Render services via API"
    echo "  railway  — Set env vars on Railway via CLI"
    echo "  aws      — Store secrets in AWS SSM Parameter Store"
    echo "  all      — Run all three"
    echo ""
    echo "Environment:"
    echo "  RENDER_API_KEY  — Required for 'render' (get from Render dashboard)"
    echo ""
    echo "Note: DATABASE_URL and REDIS_URL are auto-provided by platform add-ons."
    echo "      Do NOT set them manually."
}

case "${1:-all}" in
    render)  set_render_secrets ;;
    railway) set_railway_secrets ;;
    aws)     set_aws_secrets ;;
    all)
        echo "═══════════════════════════════════════════════════"
        echo " Setting secrets on all platforms"
        echo "═══════════════════════════════════════════════════"
        echo ""
        echo "── RENDER ─────────────────────────────────────────"
        set_render_secrets || true
        echo ""
        echo "── RAILWAY ────────────────────────────────────────"
        set_railway_secrets || true
        echo ""
        echo "── AWS SSM ────────────────────────────────────────"
        set_aws_secrets || true
        echo ""
        echo "═══════════════════════════════════════════════════"
        echo " Done. Review output above for any errors."
        echo "═══════════════════════════════════════════════════"
        ;;
    -h|--help) usage ;;
    *) echo "Unknown target: $1"; usage; exit 1 ;;
esac
