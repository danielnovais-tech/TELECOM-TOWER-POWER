#!/usr/bin/env bash
set -euo pipefail
# ============================================================================
# EC2 Postgres → S3 Backup (uses dedicated IAM user: telecom-tower-power-ec2-backup)
#
# Install: sudo cp scripts/pg-backup-s3.sh /usr/local/bin/pg-backup-s3.sh
#          sudo chmod 755 /usr/local/bin/pg-backup-s3.sh
#
# Credentials: /etc/pg-backup-s3.env (root-readable only)
#   AWS_ACCESS_KEY_ID=AKIAxxxxxxxxxxxxxxxx
#   AWS_SECRET_ACCESS_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
#
# Cron (as ubuntu):
#   0 3 * * * /usr/local/bin/pg-backup-s3.sh >> /var/log/pg-backup.log 2>&1
# ============================================================================

CRED_FILE="/etc/pg-backup-s3.env"
BUCKET="telecom-tower-power-results"
S3_PREFIX="backups/ec2-postgres"
RETENTION_DAYS=14
LOG_TAG="pg-backup-s3"

# ── Load dedicated backup credentials ────────────────────────────────────
if [[ ! -f "$CRED_FILE" ]]; then
  echo "[$LOG_TAG] ERROR: Credential file $CRED_FILE not found" >&2
  exit 1
fi
# shellcheck source=/dev/null
source "$CRED_FILE"

if [[ -z "${AWS_ACCESS_KEY_ID:-}" || -z "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
  echo "[$LOG_TAG] ERROR: AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY missing in $CRED_FILE" >&2
  exit 1
fi
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

# ── Dump ─────────────────────────────────────────────────────────────────
TIMESTAMP=$(date +%Y-%m-%d_%H%M)
DUMP_FILE="/tmp/towers_${TIMESTAMP}.sql.gz"

echo "[$LOG_TAG] $(date -Iseconds) Starting backup..."
docker exec postgres pg_dump -U telecom -d towers | gzip > "$DUMP_FILE"
DUMP_SIZE=$(stat -c%s "$DUMP_FILE")
echo "[$LOG_TAG] Dump size: $((DUMP_SIZE / 1024)) KB"

# ── Upload ───────────────────────────────────────────────────────────────
S3_KEY="${S3_PREFIX}/towers_${TIMESTAMP}.sql.gz"

docker run --rm \
  -e AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY \
  -v "$DUMP_FILE:/tmp/backup.sql.gz:ro" \
  amazon/aws-cli s3 cp /tmp/backup.sql.gz "s3://${BUCKET}/${S3_KEY}"

echo "[$LOG_TAG] Uploaded s3://${BUCKET}/${S3_KEY}"

# ── Prune old backups ────────────────────────────────────────────────────
CUTOFF=$(date -d "-${RETENTION_DAYS} days" +%Y-%m-%d)
echo "[$LOG_TAG] Pruning backups older than $CUTOFF..."

docker run --rm \
  -e AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY \
  amazon/aws-cli s3 ls "s3://${BUCKET}/${S3_PREFIX}/" \
  | while read -r line; do
    FILE_DATE=$(echo "$line" | awk '{print $1}')
    FILE_NAME=$(echo "$line" | awk '{print $4}')
    if [[ -n "$FILE_NAME" && "$FILE_DATE" < "$CUTOFF" ]]; then
      echo "[$LOG_TAG]   Deleting $FILE_NAME (from $FILE_DATE)"
      docker run --rm \
        -e AWS_ACCESS_KEY_ID \
        -e AWS_SECRET_ACCESS_KEY \
        amazon/aws-cli s3 rm "s3://${BUCKET}/${S3_PREFIX}/${FILE_NAME}"
    fi
  done

# ── Cleanup ──────────────────────────────────────────────────────────────
rm -f "$DUMP_FILE"
echo "[$LOG_TAG] $(date -Iseconds) Backup complete."
