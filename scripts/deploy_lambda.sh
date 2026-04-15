#!/usr/bin/env bash
set -euo pipefail
#
# Deploy Telecom Tower Power to AWS Lambda via SAM CLI.
#
# Prerequisites:
#   - AWS CLI configured (aws configure)
#   - AWS SAM CLI installed (https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
#   - Docker (for sam build)
#
# Usage:
#   ./scripts/deploy_lambda.sh                           # interactive guided deploy
#   ./scripts/deploy_lambda.sh --stage dev               # deploy to dev stage
#   ./scripts/deploy_lambda.sh --database-url "postgres+asyncpg://..."
#

STAGE="${STAGE:-prod}"
STACK_NAME="telecom-tower-power-${STAGE}"
REGION="${AWS_REGION:-us-east-1}"
DATABASE_URL="${DATABASE_URL:-}"

# Parse CLI args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage)       STAGE="$2"; STACK_NAME="telecom-tower-power-${STAGE}"; shift 2 ;;
    --region)      REGION="$2"; shift 2 ;;
    --database-url) DATABASE_URL="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

echo "==> Building SAM application..."
sam build \
  --use-container \
  --build-dir .aws-sam/build

echo "==> Deploying stack '${STACK_NAME}' to ${REGION}..."

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

echo ""
echo "==> Deployment complete. Outputs:"
aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --query "Stacks[0].Outputs" \
  --output table
