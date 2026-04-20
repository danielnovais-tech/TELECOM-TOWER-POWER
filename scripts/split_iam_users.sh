#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Split shared IAM user into two purpose-scoped users
#
# Before:
#   telecom-tower-power-render — S3 batch-results + EC2 backup (shared)
#
# After:
#   telecom-tower-power-render    — S3 batch-results only (Railway/ECS)
#   telecom-tower-power-ec2-backup — S3 backups only (EC2 cron)
#
# Usage:
#   ./scripts/split_iam_users.sh
#
# Prerequisites:
#   - AWS CLI configured with IAM admin permissions
#   - After running: rotate app credentials, deploy new EC2 backup creds
# ============================================================================

BUCKET="telecom-tower-power-results"
REGION="sa-east-1"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --no-cli-pager)

RENDER_USER="telecom-tower-power-render"
BACKUP_USER="telecom-tower-power-ec2-backup"

echo "=== IAM User Separation ==="
echo "Account: $ACCOUNT_ID"
echo ""

# ── 1. Tighten the existing Render user to batch-results only ────────────
echo "▸ Step 1: Tighten $RENDER_USER to batch-results/ prefix only"

RENDER_POLICY_NAME="telecom-tower-power-s3-batch-results"

RENDER_POLICY_DOC=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListBucketBatchResults",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::${BUCKET}",
      "Condition": {
        "StringLike": {
          "s3:prefix": "batch-results/*"
        }
      }
    },
    {
      "Sid": "ReadWriteBatchResults",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::${BUCKET}/batch-results/*"
    }
  ]
}
EOF
)

# Remove all existing inline policies from the Render user
echo "  Removing old inline policies from $RENDER_USER..."
for policy_name in $(aws iam list-user-policies \
    --user-name "$RENDER_USER" \
    --query 'PolicyNames[]' --output text --no-cli-pager 2>/dev/null); do
  echo "    Deleting inline policy: $policy_name"
  aws iam delete-user-policy \
      --user-name "$RENDER_USER" \
      --policy-name "$policy_name" \
      --no-cli-pager
done

# Detach any managed policies
for policy_arn in $(aws iam list-attached-user-policies \
    --user-name "$RENDER_USER" \
    --query 'AttachedPolicies[].PolicyArn' --output text --no-cli-pager 2>/dev/null); do
  echo "    Detaching managed policy: $policy_arn"
  aws iam detach-user-policy \
      --user-name "$RENDER_USER" \
      --policy-arn "$policy_arn" \
      --no-cli-pager
done

# Apply the scoped inline policy
aws iam put-user-policy \
    --user-name "$RENDER_USER" \
    --policy-name "$RENDER_POLICY_NAME" \
    --policy-document "$RENDER_POLICY_DOC" \
    --no-cli-pager
echo "  ✓ $RENDER_USER now scoped to s3://$BUCKET/batch-results/* only"
echo ""

# ── 2. Create a new backup-only IAM user ─────────────────────────────────
echo "▸ Step 2: Create $BACKUP_USER for EC2 Postgres backups"

BACKUP_POLICY_NAME="telecom-tower-power-s3-backups"

BACKUP_POLICY_DOC=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListBucketBackups",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::${BUCKET}",
      "Condition": {
        "StringLike": {
          "s3:prefix": "backups/*"
        }
      }
    },
    {
      "Sid": "ReadWriteDeleteBackups",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::${BUCKET}/backups/*"
    }
  ]
}
EOF
)

# Create user (skip if already exists)
if aws iam get-user --user-name "$BACKUP_USER" --no-cli-pager &>/dev/null; then
  echo "  User $BACKUP_USER already exists — updating policy"
else
  aws iam create-user --user-name "$BACKUP_USER" --no-cli-pager --output text \
      --query 'User.Arn'
  echo "  ✓ Created IAM user: $BACKUP_USER"
fi

# Apply inline policy
aws iam put-user-policy \
    --user-name "$BACKUP_USER" \
    --policy-name "$BACKUP_POLICY_NAME" \
    --policy-document "$BACKUP_POLICY_DOC" \
    --no-cli-pager
echo "  ✓ $BACKUP_USER scoped to s3://$BUCKET/backups/* only"

# Create access key
echo ""
echo "▸ Step 3: Create access key for $BACKUP_USER"

KEY_OUTPUT=$(aws iam create-access-key \
    --user-name "$BACKUP_USER" \
    --query 'AccessKey.[AccessKeyId,SecretAccessKey]' \
    --output text --no-cli-pager)

BACKUP_KEY_ID=$(echo "$KEY_OUTPUT" | awk '{print $1}')
BACKUP_SECRET=$(echo "$KEY_OUTPUT" | awk '{print $2}')

echo "  ✓ Access key created"
echo ""
echo "================================================================"
echo "  BACKUP IAM CREDENTIALS (save these — shown only once)"
echo "================================================================"
echo "  AWS_ACCESS_KEY_ID:     $BACKUP_KEY_ID"
echo "  AWS_SECRET_ACCESS_KEY: $BACKUP_SECRET"
echo "================================================================"
echo ""

# ── 3. Store backup creds in SSM for reference ──────────────────────────
echo "▸ Step 4: Store backup credentials in SSM Parameter Store"

aws ssm put-parameter \
    --name "/telecom-tower-power/prod/EC2_BACKUP_AWS_ACCESS_KEY_ID" \
    --value "$BACKUP_KEY_ID" \
    --type SecureString \
    --overwrite \
    --no-cli-pager --output text --query 'Version' | xargs -I{} echo "  → /telecom-tower-power/prod/EC2_BACKUP_AWS_ACCESS_KEY_ID  version {}"

aws ssm put-parameter \
    --name "/telecom-tower-power/prod/EC2_BACKUP_AWS_SECRET_ACCESS_KEY" \
    --value "$BACKUP_SECRET" \
    --type SecureString \
    --overwrite \
    --no-cli-pager --output text --query 'Version' | xargs -I{} echo "  → /telecom-tower-power/prod/EC2_BACKUP_AWS_SECRET_ACCESS_KEY  version {}"

echo ""

# ── 4. Print next steps ─────────────────────────────────────────────────
cat <<'NEXT'
▸ Next steps:

  1. SSH into the EC2 instance and update the backup script credentials:

       ssh -i ~/.ssh/telecom-tower-power-ec2.pem ubuntu@18.229.14.122

     Edit /usr/local/bin/pg-backup-s3.sh — replace the shared credentials
     with the new EC2_BACKUP_* values printed above.

     Or copy the env file approach:
       sudo tee /etc/pg-backup-s3.env > /dev/null <<EOF
       AWS_ACCESS_KEY_ID=<BACKUP_KEY_ID>
       AWS_SECRET_ACCESS_KEY=<BACKUP_SECRET>
       EOF
       sudo chmod 600 /etc/pg-backup-s3.env

  2. Rotate the app user's access key (optional but recommended):

       aws iam create-access-key --user-name telecom-tower-power-render
       # Update Railway/ECS with new key
       # Then delete the old key:
       aws iam delete-access-key --user-name telecom-tower-power-render \
           --access-key-id <OLD_KEY_ID>

  3. Verify backup still works:

       sudo -u ubuntu /usr/local/bin/pg-backup-s3.sh
       aws s3 ls s3://telecom-tower-power-results/backups/ec2-postgres/

NEXT
echo "✓ IAM separation complete."
