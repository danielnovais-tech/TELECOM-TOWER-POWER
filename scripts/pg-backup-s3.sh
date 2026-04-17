#!/usr/bin/env bash
set -euo pipefail
# ============================================================================
# EC2 Postgres → S3 Backup
#
# Credentials (in order of preference):
#   1. EC2 instance profile (telecom-tower-power-ec2 role) — auto-rotating,
#      no static keys on disk. Requires 'aws' CLI installed on the host.
#   2. /etc/pg-backup-s3.env static key fallback (telecom-tower-power-ec2-backup
#      IAM user) — only used when instance metadata is unavailable.
#
# Install: sudo cp scripts/pg-backup-s3.sh /usr/local/bin/pg-backup-s3.sh
#          sudo chmod 755 /usr/local/bin/pg-backup-s3.sh
#
# Cron (as ubuntu):
#   0 3 * * * /usr/local/bin/pg-backup-s3.sh >> /var/log/pg-backup.log 2>&1
# ============================================================================

BUCKET="telecom-tower-power-results"
S3_PREFIX="backups/ec2-postgres"
RETENTION_DAYS=14
LOG_TAG="pg-backup-s3"
CRED_FILE="/etc/pg-backup-s3.env"
SLACK_WEBHOOK_FILE="/run/secrets/slack_webhook_url"

# ── Slack notification helper ────────────────────────────────────────────
_slack_url=""
if [[ -f "$SLACK_WEBHOOK_FILE" ]]; then
  _slack_url=$(cat "$SLACK_WEBHOOK_FILE")
elif [[ -n "${SLACK_WEBHOOK_URL:-}" ]]; then
  _slack_url="$SLACK_WEBHOOK_URL"
fi

notify_slack() {
  local emoji="$1" msg="$2"
  if [[ -z "$_slack_url" ]]; then return 0; fi
  curl -sf -X POST "$_slack_url" \
    -H 'Content-Type: application/json' \
    -d "{\"text\":\"${emoji} *pg-backup-s3*: ${msg}\"}" \
    >/dev/null 2>&1 || true
}

# Notify on any non-zero exit (trap fires before set -e aborts)
trap 'notify_slack ":x:" "Backup FAILED (exit $?) at $(date -Iseconds). Check /var/log/pg-backup.log"' ERR

# ── Resolve AWS credentials ──────────────────────────────────────────────
# Prefer instance profile (IMDS) — requires host-installed aws CLI.
# Fall back to static keys from env file if IMDS is unreachable.
if command -v aws &>/dev/null && aws sts get-caller-identity &>/dev/null 2>&1; then
  echo "[$LOG_TAG] Using EC2 instance profile credentials"
  USE_HOST_AWS=true
elif [[ -f "$CRED_FILE" ]]; then
  echo "[$LOG_TAG] IMDS unavailable — falling back to $CRED_FILE"
  # shellcheck source=/dev/null
  source "$CRED_FILE"
  if [[ -z "${AWS_ACCESS_KEY_ID:-}" || -z "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
    echo "[$LOG_TAG] ERROR: Keys missing in $CRED_FILE" >&2
    exit 1
  fi
  export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
  USE_HOST_AWS=false
else
  echo "[$LOG_TAG] ERROR: No instance profile and no $CRED_FILE found" >&2
  exit 1
fi

# Helper: run aws CLI (host-native or Docker fallback)
run_aws() {
  if [[ "$USE_HOST_AWS" == true ]]; then
    aws "$@"
  else
    docker run --rm \
      -e AWS_ACCESS_KEY_ID \
      -e AWS_SECRET_ACCESS_KEY \
      amazon/aws-cli "$@"
  fi
}

# ── Dump ─────────────────────────────────────────────────────────────────
PG_CONTAINER="${PG_CONTAINER:-telecom-tower-power-postgres-1}"
TIMESTAMP=$(date +%Y-%m-%d_%H%M)
DUMP_FILE="/tmp/towers_${TIMESTAMP}.sql.gz"

echo "[$LOG_TAG] $(date -Iseconds) Starting backup..."
docker exec "$PG_CONTAINER" pg_dump -U telecom -d towers | gzip > "$DUMP_FILE"
DUMP_SIZE=$(stat -c%s "$DUMP_FILE")
echo "[$LOG_TAG] Dump size: $((DUMP_SIZE / 1024)) KB"

# ── Upload ───────────────────────────────────────────────────────────────
S3_KEY="${S3_PREFIX}/towers_${TIMESTAMP}.sql.gz"

if [[ "$USE_HOST_AWS" == true ]]; then
  run_aws s3 cp "$DUMP_FILE" "s3://${BUCKET}/${S3_KEY}"
else
  docker run --rm \
    -e AWS_ACCESS_KEY_ID \
    -e AWS_SECRET_ACCESS_KEY \
    -v "$DUMP_FILE:/tmp/backup.sql.gz:ro" \
    amazon/aws-cli s3 cp /tmp/backup.sql.gz "s3://${BUCKET}/${S3_KEY}"
fi

echo "[$LOG_TAG] Uploaded s3://${BUCKET}/${S3_KEY}"

# ── Prune old backups ────────────────────────────────────────────────────
CUTOFF=$(date -d "-${RETENTION_DAYS} days" +%Y-%m-%d)
echo "[$LOG_TAG] Pruning backups older than $CUTOFF..."

run_aws s3 ls "s3://${BUCKET}/${S3_PREFIX}/" \
  | while read -r line; do
    FILE_DATE=$(echo "$line" | awk '{print $1}')
    FILE_NAME=$(echo "$line" | awk '{print $4}')
    if [[ -n "$FILE_NAME" && "$FILE_DATE" < "$CUTOFF" ]]; then
      echo "[$LOG_TAG]   Deleting $FILE_NAME (from $FILE_DATE)"
      run_aws s3 rm "s3://${BUCKET}/${S3_PREFIX}/${FILE_NAME}"
    fi
  done

# ── Cleanup ──────────────────────────────────────────────────────────────
rm -f "$DUMP_FILE"
echo "[$LOG_TAG] $(date -Iseconds) Backup complete."
notify_slack ":white_check_mark:" "Backup OK — s3://${BUCKET}/${S3_KEY} ($((DUMP_SIZE / 1024)) KB)"
