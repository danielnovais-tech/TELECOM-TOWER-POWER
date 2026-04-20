#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Telecom Tower Power — Environment Secrets Setup
# Sets secrets on: Railway (CLI), AWS (SSM Parameter Store)
# ============================================================================

# ── Secret Values ────────────────────────────────────────────────────────────
# Replace these with real values in production
#
# Tier requirements:
#   Free tier:         VALID_API_KEYS (+ DATABASE_URL, REDIS_URL auto-provided)
#   Pro/Enterprise:    All of the above + S3_*, STRIPE_*, AWS_*
#
# Note: DATABASE_URL and REDIS_URL are auto-injected by platform add-ons
#       (Railway Postgres plugin, etc.) — do NOT set manually.

# ── Required for ALL tiers ─────────────────────────────────────────
VALID_API_KEYS='{"ttp_free_c35024654d83b243d3132064dfbde04b":"free","demo-key-free-001":"free"}'

# ── Required for Pro/Enterprise only ───────────────────────────────
STRIPE_SECRET_KEY="sk_live_REPLACE_ME"
STRIPE_WEBHOOK_SECRET="whsec_REPLACE_ME"
STRIPE_PRICE_PRO="price_1TLuIl3HxrWvYaypEFUDbR58"
STRIPE_PRICE_ENTERPRISE="price_1TLuJp3HxrWvYaypRCcqZr4g"

# AWS credentials — IAM user for S3 access
# Scoped to s3://telecom-tower-power-results/batch-results/* ONLY.
# EC2 backups use a SEPARATE IAM user (telecom-tower-power-ec2-backup)
# with credentials stored in /etc/pg-backup-s3.env on the EC2 instance.
# See: scripts/split_iam_users.sh
AWS_ACCESS_KEY_ID="REPLACE_ME"
AWS_SECRET_ACCESS_KEY="REPLACE_ME"
S3_BUCKET_NAME="telecom-tower-power-results"
S3_REGION="sa-east-1"

# ── App config ───────────────────────────────────────────────────
FRONTEND_URL="https://app.telecomtowerpower.com.br"
CORS_ORIGINS="https://app.telecomtowerpower.com.br,https://www.telecomtowerpower.com.br,https://api.telecomtowerpower.com.br,https://app.telecomtowerpower.com"
MAX_BATCH_ROWS="500"

# ── AWS SSM prefix ───────────────────────────────────────────────────────────
SSM_PREFIX="/telecom-tower-power/prod"

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
    echo "Usage: $0 [railway|aws|all]"
    echo ""
    echo "  railway  — Set env vars on Railway via CLI"
    echo "  aws      — Store secrets in AWS SSM Parameter Store"
    echo "  all      — Run both"
    echo ""
    echo "Note: DATABASE_URL and REDIS_URL are auto-provided by platform add-ons."
    echo "      Do NOT set them manually."
}

case "${1:-all}" in
    railway) set_railway_secrets ;;
    aws)     set_aws_secrets ;;
    all)
        echo "═══════════════════════════════════════════════════"
        echo " Setting secrets on all platforms"
        echo "═══════════════════════════════════════════════════"
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
