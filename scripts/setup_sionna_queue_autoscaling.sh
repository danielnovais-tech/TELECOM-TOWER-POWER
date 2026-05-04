#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
#
# setup_sionna_queue_autoscaling.sh
#
# Auto-scales AWS Batch CE maxvCpus from SQS queue depth.
#
# AWS Batch does NOT permit manual desiredvCpus updates on managed CEs, so
# this script gates capacity by adjusting maxvCpus instead. minvCpus stays at
# 0; Batch's native auto-scaler handles desiredvCpus once jobs are submitted.
#
# Flow:
#   CloudWatch Alarm (SQS visible messages > threshold)
#      -> EventBridge state-change rule (ALARM) -> Lambda scaler maxvCpus=scale_up_vcpus
#      -> EventBridge state-change rule (OK)    -> Lambda scaler maxvCpus=scale_down_vcpus
#
# Idempotent: safe to re-run.
#
# Usage:
#   ./scripts/setup_sionna_queue_autoscaling.sh \
#     --account-id 490083271496 \
#     --region sa-east-1 \
#     --queue-name telecom-tower-power-rt-jobs \
#     --compute-environment ce-sionna-rt-g5
#
# Optional tuning:
#   --scale-up-vcpus 8      (default)
#   --scale-down-vcpus 0    (default)
#   --alarm-threshold 0     (default; >0 means at least one visible message)
#   --alarm-period 60       (seconds)
#   --alarm-eval-periods 2
set -euo pipefail

ACCOUNT_ID="${AWS_ACCOUNT_ID:-}"
REGION="${AWS_REGION:-sa-east-1}"
QUEUE_NAME="${QUEUE_NAME:-telecom-tower-power-rt-jobs}"
CE_NAME="${CE_NAME:-ce-sionna-rt-g5}"
SCALE_UP_VCPUS="${SCALE_UP_VCPUS:-8}"
SCALE_DOWN_VCPUS="${SCALE_DOWN_VCPUS:-0}"
ALARM_THRESHOLD="${ALARM_THRESHOLD:-0}"
ALARM_PERIOD="${ALARM_PERIOD:-60}"
ALARM_EVAL_PERIODS="${ALARM_EVAL_PERIODS:-2}"
DRY_RUN="false"

SCALER_ROLE_NAME="${SCALER_ROLE_NAME:-telecom-tower-power-rt-scaler-role}"
SCALER_FUNCTION_NAME="${SCALER_FUNCTION_NAME:-telecom-tower-power-rt-queue-scaler}"
ALARM_NAME="${ALARM_NAME:-sionna-rt-queue-backlog}"
RULE_UP_NAME="${RULE_UP_NAME:-sionna-rt-scale-up-on-backlog}"
RULE_DOWN_NAME="${RULE_DOWN_NAME:-sionna-rt-scale-down-on-empty}"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/setup_sionna_queue_autoscaling.sh \
    --account-id 490083271496 \
    --region sa-east-1 \
    --queue-name telecom-tower-power-rt-jobs \
    --compute-environment ce-sionna-rt-g5

Options:
  --scale-up-vcpus <n>        maxvCpus when alarm is ALARM (default: 8)
  --scale-down-vcpus <n>      maxvCpus when alarm returns OK (default: 0)
  --alarm-threshold <n>       SQS visible-message threshold (default: 0)
  --alarm-period <seconds>    CloudWatch period (default: 60)
  --alarm-eval-periods <n>    CloudWatch evaluation periods (default: 2)
  --scaler-role-name <name>
  --scaler-function-name <name>
  --alarm-name <name>
  --rule-up-name <name>
  --rule-down-name <name>
  --dry-run
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --account-id) ACCOUNT_ID="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --queue-name) QUEUE_NAME="$2"; shift 2 ;;
    --compute-environment) CE_NAME="$2"; shift 2 ;;
    --scale-up-vcpus) SCALE_UP_VCPUS="$2"; shift 2 ;;
    --scale-down-vcpus) SCALE_DOWN_VCPUS="$2"; shift 2 ;;
    --alarm-threshold) ALARM_THRESHOLD="$2"; shift 2 ;;
    --alarm-period) ALARM_PERIOD="$2"; shift 2 ;;
    --alarm-eval-periods) ALARM_EVAL_PERIODS="$2"; shift 2 ;;
    --scaler-role-name) SCALER_ROLE_NAME="$2"; shift 2 ;;
    --scaler-function-name) SCALER_FUNCTION_NAME="$2"; shift 2 ;;
    --alarm-name) ALARM_NAME="$2"; shift 2 ;;
    --rule-up-name) RULE_UP_NAME="$2"; shift 2 ;;
    --rule-down-name) RULE_DOWN_NAME="$2"; shift 2 ;;
    --dry-run) DRY_RUN="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$ACCOUNT_ID" ]]; then
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
fi

: "${ACCOUNT_ID:?--account-id required (or set AWS_ACCOUNT_ID)}"

if (( SCALE_DOWN_VCPUS < 0 )) || (( SCALE_UP_VCPUS < 0 )); then
  echo "ERROR: scale values must be >= 0" >&2
  exit 2
fi

if (( SCALE_DOWN_VCPUS > SCALE_UP_VCPUS )); then
  echo "ERROR: --scale-down-vcpus cannot be greater than --scale-up-vcpus" >&2
  exit 2
fi

run() {
  if [[ "$DRY_RUN" == "true" ]]; then
    printf 'DRY-RUN: %s\n' "$*"
  else
    "$@"
  fi
}

echo "=================================================="
echo " Sionna RT Queue Autoscaling"
echo " Account:        $ACCOUNT_ID"
echo " Region:         $REGION"
echo " Queue:          $QUEUE_NAME"
echo " CE:             $CE_NAME"
echo " Scale up:       $SCALE_UP_VCPUS vCPUs"
echo " Scale down:     $SCALE_DOWN_VCPUS vCPUs"
echo " Alarm:          $ALARM_NAME (threshold > $ALARM_THRESHOLD)"
echo " Lambda:         $SCALER_FUNCTION_NAME"
echo " Dry-run:        $DRY_RUN"
echo "=================================================="

# ── 0) SQS queue (create if missing) ────────────────────────────────
echo "[0/6] SQS queue $QUEUE_NAME"
QUEUE_URL=$(aws sqs get-queue-url --queue-name "$QUEUE_NAME" --region "$REGION" \
  --query QueueUrl --output text 2>/dev/null || true)
if [[ -z "$QUEUE_URL" || "$QUEUE_URL" == "None" ]]; then
  echo "  queue not found — creating DLQ + main queue"
  DLQ_NAME="${QUEUE_NAME}-dlq"
  if [[ "$DRY_RUN" != "true" ]]; then
    DLQ_URL=$(aws sqs get-queue-url --queue-name "$DLQ_NAME" --region "$REGION" \
      --query QueueUrl --output text 2>/dev/null || true)
    if [[ -z "$DLQ_URL" || "$DLQ_URL" == "None" ]]; then
      aws sqs create-queue --queue-name "$DLQ_NAME" --region "$REGION" \
        --attributes MessageRetentionPeriod=1209600 >/dev/null
      DLQ_URL=$(aws sqs get-queue-url --queue-name "$DLQ_NAME" --region "$REGION" \
        --query QueueUrl --output text)
    fi
    DLQ_ARN=$(aws sqs get-queue-attributes --queue-url "$DLQ_URL" --region "$REGION" \
      --attribute-names QueueArn --query 'Attributes.QueueArn' --output text)
    _ATTRS_FILE=$(mktemp /tmp/sqs-attrs-XXXXXX.json)
    python3 - "$DLQ_ARN" > "$_ATTRS_FILE" <<'PYEOF'
import json, sys
dlq_arn = sys.argv[1]
print(json.dumps({
    "VisibilityTimeout": "900",
    "MessageRetentionPeriod": "86400",
    "RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "5"}),
}))
PYEOF
    aws sqs create-queue --queue-name "$QUEUE_NAME" --region "$REGION" \
      --attributes "file://${_ATTRS_FILE}" >/dev/null
    rm -f "$_ATTRS_FILE"
    QUEUE_URL=$(aws sqs get-queue-url --queue-name "$QUEUE_NAME" --region "$REGION" \
      --query QueueUrl --output text)
    echo "  created: $QUEUE_URL"
  else
    printf 'DRY-RUN: aws sqs create-queue --queue-name %s-dlq --region %s\n' "$QUEUE_NAME" "$REGION"
    printf 'DRY-RUN: aws sqs create-queue --queue-name %s --region %s\n' "$QUEUE_NAME" "$REGION"
    QUEUE_URL="https://sqs.${REGION}.amazonaws.com/${ACCOUNT_ID}/${QUEUE_NAME}"
    echo "  (dry-run) would create: $QUEUE_URL"
  fi
else
  echo "  exists: $QUEUE_URL"
fi

# Validate CE exists early.
CE_STATUS=$(aws batch describe-compute-environments --region "$REGION" \
  --compute-environments "$CE_NAME" --query 'computeEnvironments[0].status' --output text 2>/dev/null || true)
if [[ -z "$CE_STATUS" || "$CE_STATUS" == "None" ]]; then
  echo "ERROR: compute environment '$CE_NAME' not found in $REGION" >&2
  exit 1
fi

ROLE_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${SCALER_ROLE_NAME}"
FUNC_ARN="arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${SCALER_FUNCTION_NAME}"
RULE_UP_ARN="arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/${RULE_UP_NAME}"
RULE_DOWN_ARN="arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/${RULE_DOWN_NAME}"

# 1) IAM role for scaler Lambda.
echo "[1/6] IAM role $SCALER_ROLE_NAME"
if ! aws iam get-role --role-name "$SCALER_ROLE_NAME" >/dev/null 2>&1; then
  run aws iam create-role --role-name "$SCALER_ROLE_NAME" \
    --assume-role-policy-document "$ROLE_TRUST" >/dev/null
fi

SCALER_POLICY_DOC=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["batch:DescribeComputeEnvironments", "batch:UpdateComputeEnvironment"],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:${REGION}:${ACCOUNT_ID}:*"
    }
  ]
}
EOF
)
run aws iam put-role-policy --role-name "$SCALER_ROLE_NAME" \
  --policy-name "${SCALER_ROLE_NAME}-inline" \
  --policy-document "$SCALER_POLICY_DOC"

# 2) Lambda function (create or update).
echo "[2/6] Lambda scaler $SCALER_FUNCTION_NAME"
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

cat > "$TMP_DIR/index.py" <<'PY'
import logging
import os

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)


def handler(event, context):
    """Gate AWS Batch CE capacity by adjusting maxvCpus.

    AWS Batch does NOT allow manual updates to desiredvCpus on managed CEs
    ("Manually scaling down compute environment is not supported"). The
    supported pattern is to change maxvCpus: setting it to 0 forces Batch
    to scale down to minvCpus (=0); raising it to N allows Batch's native
    auto-scaler to launch up to N vCPUs when jobs are queued.
    """
    ce_name = os.environ["CE_NAME"]
    region = os.environ.get("AWS_REGION")
    default_target = int(os.environ.get("DEFAULT_TARGET_VCPUS", "0"))

    # Accept legacy "desired_vcpus" key for backwards compatibility.
    target_raw = event.get("target_vcpus", event.get("desired_vcpus", default_target))
    target = int(target_raw)
    if target < 0:
        target = 0

    batch = boto3.client("batch", region_name=region)
    desc = batch.describe_compute_environments(computeEnvironments=[ce_name])
    envs = desc.get("computeEnvironments") or []
    if not envs:
        raise RuntimeError(f"Compute environment not found: {ce_name}")

    cr = envs[0].get("computeResources") or {}
    current_max = int(cr.get("maxvCpus", 0))
    current_desired = int(cr.get("desiredvCpus", 0))

    if current_max == target:
        log.info("No change needed (maxvCpus already %s)", current_max)
        return {
            "changed": False,
            "compute_environment": ce_name,
            "old_max_vcpus": current_max,
            "new_max_vcpus": target,
            "current_desired_vcpus": current_desired,
        }

    batch.update_compute_environment(
        computeEnvironment=ce_name,
        computeResources={"maxvCpus": target},
    )
    log.info("Updated %s maxvCpus: %s -> %s", ce_name, current_max, target)
    return {
        "changed": True,
        "compute_environment": ce_name,
        "old_max_vcpus": current_max,
        "new_max_vcpus": target,
        "current_desired_vcpus": current_desired,
    }
PY

(
  cd "$TMP_DIR"
  python3 -m zipfile -c function.zip index.py >/dev/null
)

FUNC_EXISTS=$(aws lambda get-function --function-name "$SCALER_FUNCTION_NAME" \
  --region "$REGION" >/dev/null 2>&1; echo $?)

if [[ "$FUNC_EXISTS" -ne 0 ]]; then
  run aws lambda create-function \
    --function-name "$SCALER_FUNCTION_NAME" \
    --runtime python3.11 \
    --handler index.handler \
    --role "$ROLE_ARN" \
    --timeout 30 \
    --memory-size 128 \
    --environment "Variables={CE_NAME=${CE_NAME},DEFAULT_TARGET_VCPUS=${SCALE_DOWN_VCPUS}}" \
    --zip-file "fileb://$TMP_DIR/function.zip" \
    --region "$REGION" >/dev/null
else
  run aws lambda update-function-code \
    --function-name "$SCALER_FUNCTION_NAME" \
    --zip-file "fileb://$TMP_DIR/function.zip" \
    --region "$REGION" >/dev/null
  run aws lambda update-function-configuration \
    --function-name "$SCALER_FUNCTION_NAME" \
    --timeout 30 \
    --memory-size 128 \
    --runtime python3.11 \
    --handler index.handler \
    --environment "Variables={CE_NAME=${CE_NAME},DEFAULT_TARGET_VCPUS=${SCALE_DOWN_VCPUS}}" \
    --region "$REGION" >/dev/null
fi

# 3) CloudWatch alarm on queue backlog.
echo "[3/6] CloudWatch alarm $ALARM_NAME"
run aws cloudwatch put-metric-alarm \
  --region "$REGION" \
  --alarm-name "$ALARM_NAME" \
  --alarm-description "Sionna RT queue backlog alarm (drives CE maxvCpus gating)" \
  --namespace AWS/SQS \
  --metric-name ApproximateNumberOfMessagesVisible \
  --dimensions "Name=QueueName,Value=${QUEUE_NAME}" \
  --statistic Average \
  --period "$ALARM_PERIOD" \
  --evaluation-periods "$ALARM_EVAL_PERIODS" \
  --threshold "$ALARM_THRESHOLD" \
  --comparison-operator GreaterThanThreshold \
  --treat-missing-data notBreaching

# 4) EventBridge rules for alarm state transitions.
echo "[4/6] EventBridge rules ($RULE_UP_NAME / $RULE_DOWN_NAME)"
PATTERN_UP=$(cat <<EOF
{"source":["aws.cloudwatch"],"detail-type":["CloudWatch Alarm State Change"],"detail":{"alarmName":["${ALARM_NAME}"],"state":{"value":["ALARM"]}}}
EOF
)
PATTERN_DOWN=$(cat <<EOF
{"source":["aws.cloudwatch"],"detail-type":["CloudWatch Alarm State Change"],"detail":{"alarmName":["${ALARM_NAME}"],"state":{"value":["OK"]}}}
EOF
)

run aws events put-rule --name "$RULE_UP_NAME" --event-pattern "$PATTERN_UP" --region "$REGION" >/dev/null
run aws events put-rule --name "$RULE_DOWN_NAME" --event-pattern "$PATTERN_DOWN" --region "$REGION" >/dev/null

TARGETS_UP=$(cat <<EOF
[{"Id":"ScaleUpTarget","Arn":"${FUNC_ARN}","Input":"{\"target_vcpus\":${SCALE_UP_VCPUS}}"}]
EOF
)
TARGETS_DOWN=$(cat <<EOF
[{"Id":"ScaleDownTarget","Arn":"${FUNC_ARN}","Input":"{\"target_vcpus\":${SCALE_DOWN_VCPUS}}"}]
EOF
)
run aws events put-targets --rule "$RULE_UP_NAME" --targets "$TARGETS_UP" --region "$REGION" >/dev/null
run aws events put-targets --rule "$RULE_DOWN_NAME" --targets "$TARGETS_DOWN" --region "$REGION" >/dev/null

# 5) Allow EventBridge to invoke Lambda.
echo "[5/6] Lambda invoke permissions"
add_lambda_perm() {
  local sid="$1" src_arn="$2"
  if aws lambda get-policy --function-name "$SCALER_FUNCTION_NAME" --region "$REGION" \
       --query 'Policy' --output text 2>/dev/null | grep -q "\"Sid\":\"${sid}\""; then
    return 0
  fi
  run aws lambda add-permission \
    --function-name "$SCALER_FUNCTION_NAME" \
    --statement-id "$sid" \
    --action lambda:InvokeFunction \
    --principal events.amazonaws.com \
    --source-arn "$src_arn" \
    --region "$REGION" >/dev/null
}
add_lambda_perm "allow-events-${RULE_UP_NAME}" "$RULE_UP_ARN"
add_lambda_perm "allow-events-${RULE_DOWN_NAME}" "$RULE_DOWN_ARN"

echo
echo "Done. Queue autoscaling is configured."
echo "Alarm:      $ALARM_NAME"
echo "Scale up:   ALARM -> maxvCpus=${SCALE_UP_VCPUS}"
echo "Scale down: OK    -> maxvCpus=${SCALE_DOWN_VCPUS}"
echo
echo "Quick checks:"
echo "  aws cloudwatch describe-alarms --region $REGION --alarm-names $ALARM_NAME"
echo "  aws events list-targets-by-rule --region $REGION --rule $RULE_UP_NAME"
echo "  aws batch describe-compute-environments --region $REGION --compute-environments $CE_NAME --query 'computeEnvironments[0].computeResources.[minvCpus,desiredvCpus,maxvCpus]' --output text"