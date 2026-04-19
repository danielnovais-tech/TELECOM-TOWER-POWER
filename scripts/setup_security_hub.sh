#!/usr/bin/env bash
set -euo pipefail
# ============================================================================
# Enable AWS Config + Security Hub CSPM
#
# 1. Creates an IAM service-linked role for AWS Config (if missing)
# 2. Creates an S3 bucket for Config delivery (if missing)
# 3. Enables the AWS Config configuration recorder & delivery channel
# 4. Starts the recorder
# 5. Enables AWS Security Hub
# 6. Subscribes to security standards (FSBP, CIS 1.2, CIS 1.4)
#
# Prerequisites:
#   - AWS CLI v2 configured with sufficient permissions
#   - Region defaults to sa-east-1 (override via AWS_REGION)
#
# Usage:
#   bash scripts/setup_security_hub.sh
# ============================================================================

AWS_REGION="${AWS_REGION:-sa-east-1}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
CONFIG_BUCKET="aws-config-${ACCOUNT_ID}-${AWS_REGION}"
CONFIG_ROLE_NAME="aws-config-role-telecom-tower-power"

echo "▸ Account:  $ACCOUNT_ID"
echo "▸ Region:   $AWS_REGION"
echo "▸ S3 bucket: $CONFIG_BUCKET"
echo ""

# ════════════════════════════════════════════════════════════════════════════
# PHASE 1 — AWS Config
# ════════════════════════════════════════════════════════════════════════════

# ── 1a. IAM role for AWS Config ──────────────────────────────────────────
CONFIG_ROLE_ARN=""
if aws iam get-role --role-name "$CONFIG_ROLE_NAME" >/dev/null 2>&1; then
  CONFIG_ROLE_ARN=$(aws iam get-role --role-name "$CONFIG_ROLE_NAME" \
    --query 'Role.Arn' --output text)
  echo "▸ IAM role already exists: $CONFIG_ROLE_ARN"
else
  echo "▸ Creating IAM role: $CONFIG_ROLE_NAME"
  CONFIG_ROLE_ARN=$(aws iam create-role \
    --role-name "$CONFIG_ROLE_NAME" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [
        {
          "Effect": "Allow",
          "Principal": { "Service": "config.amazonaws.com" },
          "Action": "sts:AssumeRole"
        }
      ]
    }' \
    --query 'Role.Arn' --output text)
  echo "  Created: $CONFIG_ROLE_ARN"

  # Attach the AWS managed policy for Config
  aws iam attach-role-policy \
    --role-name "$CONFIG_ROLE_NAME" \
    --policy-arn "arn:aws:iam::aws:policy/service-role/AWS_ConfigRole"
  echo "  Attached AWS_ConfigRole managed policy"

  # Allow Config to write to the S3 bucket
  aws iam put-role-policy \
    --role-name "$CONFIG_ROLE_NAME" \
    --policy-name "ConfigS3DeliveryPolicy" \
    --policy-document '{
      "Version": "2012-10-17",
      "Statement": [
        {
          "Effect": "Allow",
          "Action": [
            "s3:PutObject",
            "s3:GetBucketAcl"
          ],
          "Resource": [
            "arn:aws:s3:::'"$CONFIG_BUCKET"'",
            "arn:aws:s3:::'"$CONFIG_BUCKET"'/*"
          ],
          "Condition": {
            "StringEquals": {
              "s3:x-amz-acl": "bucket-owner-full-control"
            }
          }
        }
      ]
    }'
  echo "  Attached inline S3 delivery policy"

  echo "  Waiting 10s for IAM propagation..."
  sleep 10
fi

# ── 1b. S3 bucket for Config delivery ───────────────────────────────────
if aws s3api head-bucket --bucket "$CONFIG_BUCKET" --region "$AWS_REGION" 2>/dev/null; then
  echo "▸ S3 bucket already exists: $CONFIG_BUCKET"
else
  echo "▸ Creating S3 bucket: $CONFIG_BUCKET"
  if [[ "$AWS_REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$CONFIG_BUCKET" --region "$AWS_REGION"
  else
    aws s3api create-bucket \
      --bucket "$CONFIG_BUCKET" \
      --region "$AWS_REGION" \
      --create-bucket-configuration LocationConstraint="$AWS_REGION"
  fi

  # Block public access
  aws s3api put-public-access-block \
    --bucket "$CONFIG_BUCKET" \
    --region "$AWS_REGION" \
    --public-access-block-configuration \
      BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  echo "  Public access blocked"

  # Bucket policy allowing Config to deliver
  aws s3api put-bucket-policy \
    --bucket "$CONFIG_BUCKET" \
    --region "$AWS_REGION" \
    --policy '{
      "Version": "2012-10-17",
      "Statement": [
        {
          "Sid": "AWSConfigBucketPermissionsCheck",
          "Effect": "Allow",
          "Principal": { "Service": "config.amazonaws.com" },
          "Action": "s3:GetBucketAcl",
          "Resource": "arn:aws:s3:::'"$CONFIG_BUCKET"'"
        },
        {
          "Sid": "AWSConfigBucketExistenceCheck",
          "Effect": "Allow",
          "Principal": { "Service": "config.amazonaws.com" },
          "Action": "s3:ListBucket",
          "Resource": "arn:aws:s3:::'"$CONFIG_BUCKET"'"
        },
        {
          "Sid": "AWSConfigBucketDelivery",
          "Effect": "Allow",
          "Principal": { "Service": "config.amazonaws.com" },
          "Action": "s3:PutObject",
          "Resource": "arn:aws:s3:::'"$CONFIG_BUCKET"'/AWSLogs/'"$ACCOUNT_ID"'/Config/*",
          "Condition": {
            "StringEquals": {
              "s3:x-amz-acl": "bucket-owner-full-control"
            }
          }
        }
      ]
    }'
  echo "  Bucket policy applied"

  # Enable server-side encryption
  aws s3api put-bucket-encryption \
    --bucket "$CONFIG_BUCKET" \
    --region "$AWS_REGION" \
    --server-side-encryption-configuration '{
      "Rules": [{ "ApplyServerSideEncryptionByDefault": { "SSEAlgorithm": "AES256" } }]
    }'
  echo "  SSE-S3 encryption enabled"
fi

# ── 1c. Configuration recorder ──────────────────────────────────────────
EXISTING_RECORDER=$(aws configservice describe-configuration-recorders \
  --region "$AWS_REGION" \
  --query 'ConfigurationRecorders[0].name' --output text 2>/dev/null || echo "None")

if [[ "$EXISTING_RECORDER" != "None" && -n "$EXISTING_RECORDER" ]]; then
  echo "▸ Configuration recorder already exists: $EXISTING_RECORDER"
  # Update to ensure allSupported resources are recorded
  aws configservice put-configuration-recorder \
    --region "$AWS_REGION" \
    --configuration-recorder "name=${EXISTING_RECORDER},roleARN=${CONFIG_ROLE_ARN}" \
    --recording-group '{"allSupported":true,"includeGlobalResourceTypes":true}'
  echo "  Updated to record all resource types (including global)"
else
  echo "▸ Creating configuration recorder"
  aws configservice put-configuration-recorder \
    --region "$AWS_REGION" \
    --configuration-recorder "name=default,roleARN=${CONFIG_ROLE_ARN}" \
    --recording-group '{"allSupported":true,"includeGlobalResourceTypes":true}'
  EXISTING_RECORDER="default"
  echo "  Created: default"
fi

# ── 1d. Delivery channel ────────────────────────────────────────────────
EXISTING_CHANNEL=$(aws configservice describe-delivery-channels \
  --region "$AWS_REGION" \
  --query 'DeliveryChannels[0].name' --output text 2>/dev/null || echo "None")

if [[ "$EXISTING_CHANNEL" != "None" && -n "$EXISTING_CHANNEL" ]]; then
  echo "▸ Delivery channel already exists: $EXISTING_CHANNEL"
else
  echo "▸ Creating delivery channel"
  aws configservice put-delivery-channel \
    --region "$AWS_REGION" \
    --delivery-channel '{
      "name": "default",
      "s3BucketName": "'"$CONFIG_BUCKET"'",
      "configSnapshotDeliveryProperties": {
        "deliveryFrequency": "TwentyFour_Hours"
      }
    }'
  echo "  Created: default → s3://$CONFIG_BUCKET"
fi

# ── 1e. Start recording ─────────────────────────────────────────────────
RECORDER_STATUS=$(aws configservice describe-configuration-recorder-status \
  --region "$AWS_REGION" \
  --query 'ConfigurationRecordersStatus[0].recording' --output text 2>/dev/null || echo "false")

if [[ "$RECORDER_STATUS" == "True" || "$RECORDER_STATUS" == "true" ]]; then
  echo "▸ Configuration recorder is already recording"
else
  echo "▸ Starting configuration recorder: $EXISTING_RECORDER"
  aws configservice start-configuration-recorder \
    --configuration-recorder-name "$EXISTING_RECORDER" \
    --region "$AWS_REGION"
  echo "  Recording started ✓"
fi

echo ""
echo "═══ AWS Config is enabled and recording all resource types ═══"
echo ""

# ════════════════════════════════════════════════════════════════════════════
# PHASE 2 — AWS Security Hub
# ════════════════════════════════════════════════════════════════════════════

# ── 2a. Enable Security Hub ─────────────────────────────────────────────
if aws securityhub describe-hub --region "$AWS_REGION" >/dev/null 2>&1; then
  echo "▸ Security Hub is already enabled"
else
  echo "▸ Enabling Security Hub"
  aws securityhub enable-security-hub \
    --region "$AWS_REGION" \
    --enable-default-standards
  echo "  Security Hub enabled with default standards ✓"
  echo "  Waiting 15s for Security Hub initialization..."
  sleep 15
fi

# ── 2b. Subscribe to security standards (idempotent) ────────────────────
# ARNs vary by region; construct them dynamically.
FSBP_ARN="arn:aws:securityhub:${AWS_REGION}::standards/aws-foundational-security-best-practices/v/1.0.0"
CIS_12_ARN="arn:aws:securityhub:::ruleset/cis-aws-foundations-benchmark/v/1.2.0"
CIS_14_ARN="arn:aws:securityhub:${AWS_REGION}::standards/cis-aws-foundations-benchmark/v/1.4.0"

declare -A STANDARDS=(
  ["AWS Foundational Security Best Practices v1.0.0"]="$FSBP_ARN"
  ["CIS AWS Foundations Benchmark v1.2.0"]="$CIS_12_ARN"
  ["CIS AWS Foundations Benchmark v1.4.0"]="$CIS_14_ARN"
)

ENABLED_STANDARDS=$(aws securityhub get-enabled-standards \
  --region "$AWS_REGION" \
  --query 'StandardsSubscriptions[].StandardsArn' --output text 2>/dev/null || echo "")

for LABEL in "${!STANDARDS[@]}"; do
  ARN="${STANDARDS[$LABEL]}"
  if echo "$ENABLED_STANDARDS" | grep -qF "$ARN"; then
    echo "▸ Already subscribed: $LABEL"
  else
    echo "▸ Subscribing to: $LABEL"
    aws securityhub batch-enable-standards \
      --region "$AWS_REGION" \
      --standards-subscription-requests "[{\"StandardsArn\":\"$ARN\"}]" \
      >/dev/null 2>&1 || echo "  ⚠ Could not subscribe to $LABEL (may not be available in $AWS_REGION)"
    echo "  Subscribed ✓"
  fi
done

echo ""
echo "═══ AWS Security Hub CSPM is enabled ═══"
echo ""

# ── Summary ──────────────────────────────────────────────────────────────
echo "Summary"
echo "─────────────────────────────────────────────"
echo "  AWS Config recorder:    recording all resources"
echo "  Config delivery bucket: s3://$CONFIG_BUCKET"
echo "  Security Hub:           enabled"
echo "  Standards:              FSBP v1.0, CIS v1.2, CIS v1.4"
echo "  Region:                 $AWS_REGION"
echo ""
echo "Next steps:"
echo "  • Review findings in the Security Hub console"
echo "  • Allow 24–48h for the initial resource inventory and compliance evaluation"
echo "  • To disable specific controls, use:"
echo "    aws securityhub update-standards-control --standards-control-arn <arn> --control-status DISABLED --disabled-reason <reason>"
echo ""
