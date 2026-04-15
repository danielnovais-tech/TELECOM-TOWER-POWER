#!/usr/bin/env bash
set -euo pipefail
#
# deploy_batch_pipeline.sh – Deploy the serverless batch processing pipeline.
#
# This script deploys the full stack via SAM:
#   API Gateway → Lambda (API) → SQS → Lambda (Worker) → S3
#
# It also optionally sets up an RDS Proxy for safe Lambda→RDS connectivity.
#
# Prerequisites:
#   - AWS CLI configured (aws configure)
#   - AWS SAM CLI installed
#   - Docker (for sam build)
#
# Usage:
#   ./scripts/deploy_batch_pipeline.sh
#   ./scripts/deploy_batch_pipeline.sh --stage dev
#   ./scripts/deploy_batch_pipeline.sh --database-url "postgresql+asyncpg://..."
#

STAGE="${STAGE:-prod}"
STACK_NAME="telecom-tower-power-${STAGE}"
REGION="${AWS_REGION:-sa-east-1}"
DATABASE_URL="${DATABASE_URL:-}"

# Parse CLI args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage)        STAGE="$2"; STACK_NAME="telecom-tower-power-${STAGE}"; shift 2 ;;
    --region)       REGION="$2"; shift 2 ;;
    --database-url) DATABASE_URL="$2"; shift 2 ;;
    *)              echo "Unknown arg: $1"; exit 1 ;;
  esac
done

echo "=========================================="
echo " Telecom Tower Power – Batch Pipeline"
echo " Stage:  ${STAGE}"
echo " Region: ${REGION}"
echo " Stack:  ${STACK_NAME}"
echo "=========================================="

# ── Step 1: Build ────────────────────────────────────────────────
echo ""
echo "==> Step 1: Building SAM application..."
sam build \
  --use-container \
  --build-dir .aws-sam/build

# ── Step 2: Deploy ───────────────────────────────────────────────
echo ""
echo "==> Step 2: Deploying stack '${STACK_NAME}' to ${REGION}..."

PARAM_OVERRIDES="Stage=${STAGE}"
if [[ -n "${DATABASE_URL}" ]]; then
  PARAM_OVERRIDES="${PARAM_OVERRIDES} DatabaseUrl=${DATABASE_URL}"
fi

sam deploy \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides "${PARAM_OVERRIDES}" \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset

# ── Step 3: Print outputs ────────────────────────────────────────
echo ""
echo "==> Step 3: Stack outputs:"
aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --query "Stacks[0].Outputs" \
  --output table

# ── Step 4: Verify resources ────────────────────────────────────
echo ""
echo "==> Step 4: Verifying deployed resources..."

# Get SQS queue URL from outputs
SQS_URL=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='BatchJobQueueUrl'].OutputValue" \
  --output text 2>/dev/null || echo "")

if [[ -n "${SQS_URL}" ]]; then
  echo "  ✓ SQS Queue:  ${SQS_URL}"
  ATTRS=$(aws sqs get-queue-attributes \
    --queue-url "${SQS_URL}" \
    --attribute-names ApproximateNumberOfMessages VisibilityTimeout \
    --region "${REGION}" \
    --output json 2>/dev/null || echo "{}")
  echo "  Queue attributes: ${ATTRS}"
else
  echo "  ✗ SQS Queue URL not found in stack outputs"
fi

# Get S3 bucket name
BUCKET=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='ReportsBucketName'].OutputValue" \
  --output text 2>/dev/null || echo "")

if [[ -n "${BUCKET}" ]]; then
  echo "  ✓ S3 Bucket:  ${BUCKET}"
else
  echo "  ✗ S3 Bucket not found in stack outputs"
fi

# Get API URL
API_URL=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
  --output text 2>/dev/null || echo "")

if [[ -n "${API_URL}" ]]; then
  echo "  ✓ API URL:    ${API_URL}"
fi

# Get worker Lambda ARN
WORKER_ARN=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='BatchWorkerFunctionArn'].OutputValue" \
  --output text 2>/dev/null || echo "")

if [[ -n "${WORKER_ARN}" ]]; then
  echo "  ✓ Worker ARN: ${WORKER_ARN}"
fi

echo ""
echo "==> Deployment complete!"
echo ""
echo "Architecture deployed:"
echo "  Client → API Gateway → Lambda (API) → SQS → Lambda (Worker) → S3"
echo ""
echo "Test with:"
echo "  curl -X POST '${API_URL}batch_reports' \\"
echo "    -H 'X-API-Key: YOUR_KEY' \\"
echo "    -F 'tower_id=TOWER_ID' \\"
echo "    -F 'csv_file=@receivers.csv'"
