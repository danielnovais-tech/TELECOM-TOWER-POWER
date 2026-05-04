#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
#
# setup_sionna_batch.sh — Provision the AWS Batch GPU pool that runs
# Sionna RT 2.x ray-tracing jobs.
#
# Idempotent: each AWS resource is checked-then-updated/created. Re-runs
# are safe and only diff what changed.
#
# Resources managed:
#   1. SQS queue                 telecom-tower-power-rt-jobs (+ DLQ)
#   2. CloudWatch log group      /aws/batch/sionna-rt-worker
#   3. IAM roles                 batch service role, job role, exec role
#   4. Batch compute environment ce-sionna-rt-g5 (or --instance-family)
#                                 spot G5/G6, min=0 max=2 desired=0
#   5. Batch job queue           jq-sionna-rt
#   6. Batch job definition      sionna-rt-worker (registered from JSON)
#
# NOT created here (out of scope — needs human review):
#   - VPC, subnets, security groups (assumed to exist; pass via flags)
#   - ECR image push (handled by .github/workflows/build-gpu-image.yml)
#   - Bucket policies for SCENE_BUCKET / RESULTS_BUCKET
#
# Usage:
#   ./scripts/setup_sionna_batch.sh \
#       --account-id 490083271496 \
#       --region sa-east-1 \
#       --image-tag latest \
#       --subnets subnet-0aaa,subnet-0bbb \
#       --security-groups sg-0xxx
#
# Dry-run (prints what would be done without mutating):
#   ./scripts/setup_sionna_batch.sh --dry-run ...
set -euo pipefail

ACCOUNT_ID="${AWS_ACCOUNT_ID:-}"
REGION="${AWS_REGION:-sa-east-1}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
INSTANCE_FAMILY="${INSTANCE_FAMILY:-g5}"   # g5 (A10G) or g6 (L4)
MAX_VCPUS="${MAX_VCPUS:-32}"
SUBNETS="${SUBNETS:-}"
SECURITY_GROUPS="${SECURITY_GROUPS:-}"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --account-id)        ACCOUNT_ID="$2"; shift 2 ;;
    --region)            REGION="$2"; shift 2 ;;
    --image-tag)         IMAGE_TAG="$2"; shift 2 ;;
    --instance-family)   INSTANCE_FAMILY="$2"; shift 2 ;;
    --max-vcpus)         MAX_VCPUS="$2"; shift 2 ;;
    --subnets)           SUBNETS="$2"; shift 2 ;;
    --security-groups)   SECURITY_GROUPS="$2"; shift 2 ;;
    --dry-run)           DRY_RUN="true"; shift ;;
    -h|--help)           sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

: "${ACCOUNT_ID:?--account-id required (or set AWS_ACCOUNT_ID)}"
: "${SUBNETS:?--subnets required (comma-separated subnet IDs)}"
: "${SECURITY_GROUPS:?--security-groups required (comma-separated SG IDs)}"

case "$INSTANCE_FAMILY" in
  g5|g6) ;;
  *) echo "ERROR: --instance-family must be g5 or g6 (got $INSTANCE_FAMILY)" >&2; exit 2 ;;
esac

QUEUE_NAME="telecom-tower-power-rt-jobs"
DLQ_NAME="telecom-tower-power-rt-jobs-dlq"
LOG_GROUP="/aws/batch/sionna-rt-worker"
CE_NAME="ce-sionna-rt-${INSTANCE_FAMILY}"
JQ_NAME="jq-sionna-rt"
JD_NAME="sionna-rt-worker"
BATCH_SERVICE_ROLE="AWSServiceRoleForBatch"
INSTANCE_ROLE_NAME="telecom-tower-power-rt-instance-role"
INSTANCE_PROFILE_NAME="telecom-tower-power-rt-instance-profile"
JOB_ROLE_NAME="telecom-tower-power-rt-job-role"
EXEC_ROLE_NAME="telecom-tower-power-rt-exec-role"

echo "=================================================="
echo " Sionna RT Batch pool — sa-east-1, G-instance pool"
echo " Account:      $ACCOUNT_ID"
echo " Region:       $REGION"
echo " Family:       $INSTANCE_FAMILY (max ${MAX_VCPUS} vCPUs)"
echo " Image tag:    $IMAGE_TAG"
echo " Subnets:      $SUBNETS"
echo " SGs:          $SECURITY_GROUPS"
echo " Dry-run:      $DRY_RUN"
echo "=================================================="

run() {
  if [[ "$DRY_RUN" == "true" ]]; then
    printf 'DRY-RUN: %s\n' "$*"
  else
    "$@"
  fi
}

# ── 1) SQS DLQ + main queue ─────────────────────────────────────────
echo "[1/6] SQS queue + DLQ"
DLQ_URL=$(aws sqs get-queue-url --queue-name "$DLQ_NAME" --region "$REGION" \
            --query QueueUrl --output text 2>/dev/null || true)
if [[ -z "${DLQ_URL}" || "${DLQ_URL}" == "None" ]]; then
  echo "  creating DLQ $DLQ_NAME"
  run aws sqs create-queue --queue-name "$DLQ_NAME" --region "$REGION" \
        --attributes MessageRetentionPeriod=1209600 >/dev/null
  DLQ_URL=$(aws sqs get-queue-url --queue-name "$DLQ_NAME" --region "$REGION" \
              --query QueueUrl --output text 2>/dev/null || echo "")
fi
DLQ_ARN=""
if [[ -n "${DLQ_URL}" && "${DLQ_URL}" != "None" ]]; then
  DLQ_ARN=$(aws sqs get-queue-attributes --queue-url "$DLQ_URL" \
              --attribute-names QueueArn --region "$REGION" \
              --query 'Attributes.QueueArn' --output text)
fi

QUEUE_URL=$(aws sqs get-queue-url --queue-name "$QUEUE_NAME" --region "$REGION" \
              --query QueueUrl --output text 2>/dev/null || true)
if [[ -z "${QUEUE_URL}" || "${QUEUE_URL}" == "None" ]]; then
  echo "  creating queue $QUEUE_NAME (visibility=5400s, redrive→DLQ after 3 receives)"
  REDRIVE='{}'
  if [[ -n "$DLQ_ARN" ]]; then
    REDRIVE="{\"deadLetterTargetArn\":\"$DLQ_ARN\",\"maxReceiveCount\":\"3\"}"
  fi
  run aws sqs create-queue --queue-name "$QUEUE_NAME" --region "$REGION" \
        --attributes "VisibilityTimeout=5400,MessageRetentionPeriod=345600,RedrivePolicy=${REDRIVE}" \
        >/dev/null
fi

# ── 2) CloudWatch log group ─────────────────────────────────────────
echo "[2/6] CloudWatch log group $LOG_GROUP"
aws logs create-log-group --log-group-name "$LOG_GROUP" --region "$REGION" \
  2>/dev/null || true
run aws logs put-retention-policy --log-group-name "$LOG_GROUP" \
      --retention-in-days 14 --region "$REGION" || true

# ── 3) IAM roles ────────────────────────────────────────────────────
echo "[3/6] IAM roles"

ensure_role() {
  local name="$1" trust="$2"
  if ! aws iam get-role --role-name "$name" >/dev/null 2>&1; then
    echo "  creating role $name"
    run aws iam create-role --role-name "$name" \
          --assume-role-policy-document "$trust" >/dev/null
  fi
}

ensure_attached() {
  local role="$1" policy="$2"
  if ! aws iam list-attached-role-policies --role-name "$role" \
        --query 'AttachedPolicies[].PolicyArn' --output text \
        2>/dev/null | grep -q "$policy"; then
    run aws iam attach-role-policy --role-name "$role" --policy-arn "$policy"
  fi
}

# Instance role for the EC2 instances joining the compute env.
EC2_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
ensure_role "$INSTANCE_ROLE_NAME" "$EC2_TRUST"
ensure_attached "$INSTANCE_ROLE_NAME" "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"

if ! aws iam get-instance-profile --instance-profile-name "$INSTANCE_PROFILE_NAME" >/dev/null 2>&1; then
  echo "  creating instance profile $INSTANCE_PROFILE_NAME"
  run aws iam create-instance-profile --instance-profile-name "$INSTANCE_PROFILE_NAME" >/dev/null
  run aws iam add-role-to-instance-profile --instance-profile-name "$INSTANCE_PROFILE_NAME" \
        --role-name "$INSTANCE_ROLE_NAME" || true
fi

# ECS exec role — pulls image, ships logs.
ECS_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
ensure_role "$EXEC_ROLE_NAME" "$ECS_TRUST"
ensure_attached "$EXEC_ROLE_NAME" "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"

# Job role — what the worker code itself can do (S3 r/w, SQS recv, SSM read).
ensure_role "$JOB_ROLE_NAME" "$ECS_TRUST"
JOB_POLICY_DOC=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["sqs:ReceiveMessage","sqs:DeleteMessage","sqs:GetQueueAttributes","sqs:ChangeMessageVisibility"],
      "Resource": "arn:aws:sqs:${REGION}:${ACCOUNT_ID}:${QUEUE_NAME}"},
    {"Effect": "Allow", "Action": ["s3:GetObject","s3:ListBucket"],
      "Resource": ["arn:aws:s3:::telecom-tower-power-scenes","arn:aws:s3:::telecom-tower-power-scenes/*"]},
    {"Effect": "Allow", "Action": ["s3:PutObject","s3:AbortMultipartUpload","s3:ListBucket"],
      "Resource": ["arn:aws:s3:::telecom-tower-power-results","arn:aws:s3:::telecom-tower-power-results/*"]},
    {"Effect": "Allow", "Action": ["ssm:GetParameter","ssm:GetParameters"],
      "Resource": "arn:aws:ssm:${REGION}:${ACCOUNT_ID}:parameter/telecom-tower-power/*"}
  ]
}
EOF
)
run aws iam put-role-policy --role-name "$JOB_ROLE_NAME" \
      --policy-name "${JOB_ROLE_NAME}-inline" \
      --policy-document "$JOB_POLICY_DOC"

# ── 4) Compute environment ─────────────────────────────────────────
echo "[4/6] Batch compute environment $CE_NAME"
CE_STATE=$(aws batch describe-compute-environments \
            --compute-environments "$CE_NAME" --region "$REGION" \
            --query 'computeEnvironments[0].status' --output text 2>/dev/null || echo "MISSING")

# Convert comma-separated lists to JSON arrays for the AWS CLI.
SUBNETS_JSON=$(echo "$SUBNETS" | awk -F, '{ printf "["; for (i=1;i<=NF;i++) printf (i==1?"":",") "\""$i"\""; printf "]" }')
SGS_JSON=$(echo "$SECURITY_GROUPS" | awk -F, '{ printf "["; for (i=1;i<=NF;i++) printf (i==1?"":",") "\""$i"\""; printf "]" }')

CR_JSON=$(cat <<EOF
{
  "type": "EC2",
  "allocationStrategy": "BEST_FIT_PROGRESSIVE",
  "minvCpus": 0,
  "maxvCpus": ${MAX_VCPUS},
  "desiredvCpus": 0,
  "instanceTypes": ["${INSTANCE_FAMILY}"],
  "subnets": ${SUBNETS_JSON},
  "securityGroupIds": ${SGS_JSON},
  "instanceRole": "arn:aws:iam::${ACCOUNT_ID}:instance-profile/${INSTANCE_PROFILE_NAME}",
  "tags": {"project": "telecom-tower-power", "component": "sionna-rt-worker"}
}
EOF
)

if [[ "$CE_STATE" == "MISSING" ]]; then
  echo "  creating CE (this can take ~2 min to settle)"
  run aws batch create-compute-environment \
        --compute-environment-name "$CE_NAME" \
        --type MANAGED --state ENABLED \
        --compute-resources "$CR_JSON" \
        --service-role "arn:aws:iam::${ACCOUNT_ID}:role/aws-service-role/batch.amazonaws.com/${BATCH_SERVICE_ROLE}" \
        --region "$REGION" >/dev/null
else
  echo "  CE exists ($CE_STATE) — updating maxvCpus + instanceTypes"
  run aws batch update-compute-environment \
        --compute-environment "$CE_NAME" \
        --compute-resources "{\"maxvCpus\": ${MAX_VCPUS}, \"instanceTypes\": [\"${INSTANCE_FAMILY}\"]}" \
        --region "$REGION" >/dev/null || true
fi

# ── 5) Job queue ────────────────────────────────────────────────────
echo "[5/6] Batch job queue $JQ_NAME"
JQ_STATE=$(aws batch describe-job-queues --job-queues "$JQ_NAME" --region "$REGION" \
            --query 'jobQueues[0].status' --output text 2>/dev/null || echo "MISSING")
CEO_JSON="[{\"order\":1,\"computeEnvironment\":\"$CE_NAME\"}]"
if [[ "$JQ_STATE" == "MISSING" ]]; then
  run aws batch create-job-queue \
        --job-queue-name "$JQ_NAME" \
        --priority 1 --state ENABLED \
        --compute-environment-order "$CEO_JSON" \
        --region "$REGION" >/dev/null
else
  run aws batch update-job-queue \
        --job-queue "$JQ_NAME" \
        --compute-environment-order "$CEO_JSON" \
        --region "$REGION" >/dev/null || true
fi

# ── 6) Job definition ──────────────────────────────────────────────
echo "[6/6] Batch job definition $JD_NAME"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$(dirname "$SCRIPT_DIR")/sionna-rt-job-definition.json"
if [[ ! -f "$TEMPLATE" ]]; then
  echo "ERROR: $TEMPLATE not found" >&2
  exit 1
fi
RENDERED="$(mktemp)"
trap 'rm -f "$RENDERED"' EXIT
sed -e "s/ACCOUNT_ID/${ACCOUNT_ID}/g" \
    -e "s/AWS_REGION/${REGION}/g" \
    -e "s/IMAGE_TAG/${IMAGE_TAG}/g" \
    "$TEMPLATE" > "$RENDERED"

if [[ "$DRY_RUN" == "true" ]]; then
  echo "DRY-RUN: would register job definition from:"
  cat "$RENDERED"
else
  ARN=$(aws batch register-job-definition \
          --cli-input-json "file://$RENDERED" \
          --region "$REGION" \
          --query 'jobDefinitionArn' --output text)
  echo "  registered $ARN"
fi

echo "=================================================="
echo " Done. Submit a test job with:"
echo ""
echo "   aws batch submit-job --region $REGION \\"
echo "       --job-name sionna-rt-smoke-\$(date +%s) \\"
echo "       --job-queue $JQ_NAME \\"
echo "       --job-definition $JD_NAME"
echo "=================================================="
