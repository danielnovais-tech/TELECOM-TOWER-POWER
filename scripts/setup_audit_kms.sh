#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Provision the CMK used by audit_log envelope encryption.
#
# Creates (idempotently):
#   - A symmetric, customer-managed KMS CMK in $REGION with $ALIAS.
#   - A key policy that:
#       * grants the account root full admin (so you can rotate/edit later);
#       * grants the API runtime IAM user kms:GenerateDataKey + kms:Decrypt
#         scoped via EncryptionContext "purpose=audit_log.metadata_json".
#   - Enables automatic annual key rotation.
#
# Output:
#   - The CMK ARN, ready to set as AUDIT_KMS_KEY_ID on the EC2 host
#     (.env / SSM Parameter Store, your call — the ARN is not secret).
#
# Usage:
#   ./scripts/setup_audit_kms.sh
#   APP_IAM_USER=telecom-tower-power-render REGION=sa-east-1 \
#     ./scripts/setup_audit_kms.sh
#
# Prerequisites:
#   - AWS CLI configured with IAM admin + KMS admin permissions.
#   - The IAM user named in $APP_IAM_USER must already exist
#     (created by scripts/split_iam_users.sh).
# ============================================================================

REGION="${REGION:-sa-east-1}"
ALIAS="${ALIAS:-alias/ttp-audit-metadata}"
APP_IAM_USER="${APP_IAM_USER:-telecom-tower-power-render}"
DESCRIPTION="${DESCRIPTION:-Envelope encryption key for audit_log.metadata_json}"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --no-cli-pager)
APP_USER_ARN="arn:aws:iam::${ACCOUNT_ID}:user/${APP_IAM_USER}"

echo "=== Audit-log KMS provisioning ==="
echo "Account:   $ACCOUNT_ID"
echo "Region:    $REGION"
echo "Alias:     $ALIAS"
echo "App user:  $APP_USER_ARN"
echo ""

# Sanity: app user exists.
if ! aws iam get-user --user-name "$APP_IAM_USER" --no-cli-pager &>/dev/null; then
  echo "✗ IAM user '$APP_IAM_USER' not found. Run scripts/split_iam_users.sh first." >&2
  exit 1
fi

KEY_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Id": "ttp-audit-metadata-key-policy",
  "Statement": [
    {
      "Sid": "EnableRootAdmin",
      "Effect": "Allow",
      "Principal": { "AWS": "arn:aws:iam::${ACCOUNT_ID}:root" },
      "Action": "kms:*",
      "Resource": "*"
    },
    {
      "Sid": "AppRuntimeEnvelope",
      "Effect": "Allow",
      "Principal": { "AWS": "${APP_USER_ARN}" },
      "Action": [
        "kms:GenerateDataKey",
        "kms:Decrypt",
        "kms:DescribeKey"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "kms:EncryptionContext:purpose": "audit_log.metadata_json"
        }
      }
    }
  ]
}
EOF
)

# ── 1. Resolve or create the CMK ────────────────────────────────────────
EXISTING_KEY_ID=$(aws kms list-aliases \
  --region "$REGION" \
  --query "Aliases[?AliasName=='${ALIAS}'].TargetKeyId | [0]" \
  --output text --no-cli-pager 2>/dev/null || echo "None")

if [[ "$EXISTING_KEY_ID" != "None" && -n "$EXISTING_KEY_ID" ]]; then
  echo "▸ Alias $ALIAS already maps to key $EXISTING_KEY_ID — updating policy in place."
  KEY_ID="$EXISTING_KEY_ID"
else
  echo "▸ Creating new CMK..."
  KEY_ID=$(aws kms create-key \
    --region "$REGION" \
    --description "$DESCRIPTION" \
    --key-usage ENCRYPT_DECRYPT \
    --customer-master-key-spec SYMMETRIC_DEFAULT \
    --policy "$KEY_POLICY" \
    --tags TagKey=app,TagValue=telecom-tower-power TagKey=purpose,TagValue=audit-metadata \
    --query 'KeyMetadata.KeyId' --output text --no-cli-pager)
  echo "  ✓ Created CMK $KEY_ID"

  echo "▸ Creating alias $ALIAS"
  aws kms create-alias \
    --region "$REGION" \
    --alias-name "$ALIAS" \
    --target-key-id "$KEY_ID" \
    --no-cli-pager
fi

# ── 2. (Re)apply policy ─────────────────────────────────────────────────
echo "▸ Applying key policy"
aws kms put-key-policy \
  --region "$REGION" \
  --key-id "$KEY_ID" \
  --policy-name default \
  --policy "$KEY_POLICY" \
  --no-cli-pager

# ── 3. Enable annual rotation ───────────────────────────────────────────
echo "▸ Enabling annual key rotation"
aws kms enable-key-rotation \
  --region "$REGION" \
  --key-id "$KEY_ID" \
  --no-cli-pager 2>/dev/null || true

KEY_ARN=$(aws kms describe-key \
  --region "$REGION" \
  --key-id "$KEY_ID" \
  --query 'KeyMetadata.Arn' --output text --no-cli-pager)

echo ""
echo "=== Done ==="
echo "AUDIT_KMS_KEY_ID=$KEY_ARN"
echo "AUDIT_KMS_REGION=$REGION"
echo ""
echo "Next steps:"
echo "  1. Add to EC2 .env (or SSM):"
echo "       AUDIT_KMS_KEY_ID=$KEY_ARN"
echo "       AUDIT_KMS_REGION=$REGION"
echo "  2. Restart the api container:"
echo "       docker compose up -d api"
echo "  3. Dry-run the historical migration:"
echo "       gh workflow run audit-log-encrypt-history.yml -f dry_run=true"
echo "  4. Inspect logs, then rerun without dry_run to migrate."
