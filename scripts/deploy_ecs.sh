#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Telecom Tower Power — AWS ECS Fargate Deployment Script
#
# Prerequisites:
#   - AWS CLI v2 configured with appropriate credentials
#   - Docker installed and running
#   - SSM parameters already stored (use scripts/set_secrets.sh)
#
# Usage:
#   export AWS_ACCOUNT_ID=123456789012
#   export AWS_REGION=us-east-1          # optional, defaults to us-east-1
#   ./scripts/deploy_ecs.sh
# ============================================================================

AWS_REGION="${AWS_REGION:-sa-east-1}"
CLUSTER_NAME="telecom-tower-power"
SERVICE_NAME="telecom-tower-power"
ECR_REPO="telecom-tower-power"
LOG_GROUP="/ecs/telecom-tower-power"
TASK_DEF_FILE="ecs-task-definition.json"
IMAGE_TAG="${IMAGE_TAG:-latest}"
EFS_ID="${EFS_ID:-fs-091b9107b39a5ed53}"
AP_SRTM_ID="${AP_SRTM_ID:-fsap-0a740ba6cf0c71f41}"
AP_JOBS_ID="${AP_JOBS_ID:-fsap-02abb963dd67f4238}"

# ── Validate ─────────────────────────────────────────────────────────────────
if [[ -z "${AWS_ACCOUNT_ID:-}" ]]; then
  echo "ERROR: Set AWS_ACCOUNT_ID before running this script."
  echo "  export AWS_ACCOUNT_ID=123456789012"
  exit 1
fi

command -v aws >/dev/null 2>&1 || { echo "ERROR: AWS CLI not found. Install it first."; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "ERROR: Docker not found. Install it first."; exit 1; }

ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
FULL_IMAGE="${ECR_URI}/${ECR_REPO}:${IMAGE_TAG}"

echo "=== ECS Fargate Deployment ==="
echo "  Account:  ${AWS_ACCOUNT_ID}"
echo "  Region:   ${AWS_REGION}"
echo "  Image:    ${FULL_IMAGE}"
echo ""

# ── Step 1: Create ECR repository (idempotent) ──────────────────────────────
echo "▸ Step 1: Ensuring ECR repository exists..."
aws ecr describe-repositories --repository-names "${ECR_REPO}" --region "${AWS_REGION}" >/dev/null 2>&1 || \
  aws ecr create-repository --repository-name "${ECR_REPO}" --region "${AWS_REGION}" --image-scanning-configuration scanOnPush=true >/dev/null
echo "  ✓ ECR repository: ${ECR_REPO}"

# ── Step 2: Build & push Docker image ───────────────────────────────────────
echo "▸ Step 2: Building and pushing Docker image..."
aws ecr get-login-password --region "${AWS_REGION}" | docker login --username AWS --password-stdin "${ECR_URI}"
docker build -t "${ECR_REPO}:${IMAGE_TAG}" .
docker tag "${ECR_REPO}:${IMAGE_TAG}" "${FULL_IMAGE}"
docker push "${FULL_IMAGE}"
echo "  ✓ Image pushed: ${FULL_IMAGE}"

# ── Step 3: Create CloudWatch log group (idempotent) ─────────────────────────
echo "▸ Step 3: Ensuring CloudWatch log group exists..."
aws logs describe-log-groups --log-group-name-prefix "${LOG_GROUP}" --region "${AWS_REGION}" \
  | grep -q "${LOG_GROUP}" 2>/dev/null || \
  aws logs create-log-group --log-group-name "${LOG_GROUP}" --region "${AWS_REGION}" 2>/dev/null || true
echo "  ✓ Log group: ${LOG_GROUP}"

# ── Step 4: Create ECS cluster (idempotent) ──────────────────────────────────
echo "▸ Step 4: Ensuring ECS cluster exists..."
aws ecs describe-clusters --clusters "${CLUSTER_NAME}" --region "${AWS_REGION}" \
  | grep -q '"status": "ACTIVE"' 2>/dev/null || \
  aws ecs create-cluster --cluster-name "${CLUSTER_NAME}" --region "${AWS_REGION}" >/dev/null
echo "  ✓ Cluster: ${CLUSTER_NAME}"

# ── Step 5: Register task definition ─────────────────────────────────────────
echo "▸ Step 5: Registering task definition..."
# Replace ACCOUNT_ID and AWS_REGION placeholders with actual values
# Pipe sed directly to avoid echo mangling backslashes in healthCheck commands
TASK_ARN=$(sed -e "s/ACCOUNT_ID/${AWS_ACCOUNT_ID}/g" -e "s/AWS_REGION/${AWS_REGION}/g" -e "s/EFS_ID/${EFS_ID}/g" -e "s/AP_SRTM_ID/${AP_SRTM_ID}/g" -e "s/AP_JOBS_ID/${AP_JOBS_ID}/g" "${TASK_DEF_FILE}" \
  | aws ecs register-task-definition \
  --cli-input-json file:///dev/stdin \
  --region "${AWS_REGION}" \
  --query 'taskDefinition.taskDefinitionArn' \
  --output text)
echo "  ✓ Task definition: ${TASK_ARN}"

# ── Step 6: Create or update ECS service ─────────────────────────────────────
echo "▸ Step 6: Creating/updating ECS service..."
EXISTING_SERVICE=$(aws ecs describe-services \
  --cluster "${CLUSTER_NAME}" \
  --services "${SERVICE_NAME}" \
  --region "${AWS_REGION}" \
  --query 'services[?status==`ACTIVE`].serviceName' \
  --output text 2>/dev/null || echo "")

if [[ -n "${EXISTING_SERVICE}" && "${EXISTING_SERVICE}" != "None" ]]; then
  # Update existing service with new task definition
  aws ecs update-service \
    --cluster "${CLUSTER_NAME}" \
    --service "${SERVICE_NAME}" \
    --task-definition "${TASK_ARN}" \
    --force-new-deployment \
    --region "${AWS_REGION}" >/dev/null
  echo "  ✓ Service updated: ${SERVICE_NAME}"
else
  # Create new service — requires a subnet and security group
  if [[ -z "${SUBNET_IDS:-}" || -z "${SECURITY_GROUP_ID:-}" ]]; then
    echo ""
    echo "  ⚠  New service requires network configuration."
    echo "  Set these environment variables and re-run:"
    echo "    export SUBNET_IDS='subnet-abc123,subnet-def456'"
    echo "    export SECURITY_GROUP_ID='sg-0123456789abcdef0'"
    echo ""
    echo "  To find your default VPC subnets:"
    echo "    aws ec2 describe-subnets --filters 'Name=default-for-az,Values=true' --query 'Subnets[].SubnetId' --output text"
    echo ""
    echo "  Task definition registered successfully. Run this script again after setting network vars."
    exit 0
  fi

  aws ecs create-service \
    --cluster "${CLUSTER_NAME}" \
    --service-name "${SERVICE_NAME}" \
    --task-definition "${TASK_ARN}" \
    --desired-count 1 \
    --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[${SUBNET_IDS}],securityGroups=[${SECURITY_GROUP_ID}],assignPublicIp=ENABLED}" \
    --region "${AWS_REGION}" >/dev/null
  echo "  ✓ Service created: ${SERVICE_NAME}"
fi

# ── Step 7: Wait for deployment ──────────────────────────────────────────────
echo ""
echo "▸ Waiting for service to stabilize (this may take 2-5 minutes)..."
aws ecs wait services-stable \
  --cluster "${CLUSTER_NAME}" \
  --services "${SERVICE_NAME}" \
  --region "${AWS_REGION}" 2>/dev/null && \
  echo "  ✓ Service is stable and healthy!" || \
  echo "  ⚠  Timed out waiting. Check the ECS console for status."

echo ""
echo "=== Deployment complete ==="
echo "  Dashboard: https://${AWS_REGION}.console.aws.amazon.com/ecs/home?region=${AWS_REGION}#/clusters/${CLUSTER_NAME}/services/${SERVICE_NAME}"
echo "  Logs:      https://${AWS_REGION}.console.aws.amazon.com/cloudwatch/home?region=${AWS_REGION}#logsV2:log-groups/log-group/${LOG_GROUP}"
