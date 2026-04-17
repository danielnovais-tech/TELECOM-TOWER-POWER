#!/usr/bin/env bash
# scripts/setup_lambda_cost_alarms.sh
#
# Creates CloudWatch alarms to monitor Lambda batch worker usage
# and alert when invocations suggest a cost threshold is being approached.
#
# Alarms:
#   1. High invocation rate  — >500 invocations/day (consider reserved concurrency)
#   2. High duration         — avg >600s per invocation (approaching 15-min limit)
#   3. Throttles             — any throttles indicate concurrency exhaustion
#   4. Error rate            — >5% errors
#
# Prerequisites:
#   - aws cli configured for sa-east-1
#   - SNS topic telecom-tower-power-ec2-alarms already exists
#     (created by scripts/setup_ec2_alarms.sh)
#
# Usage:
#   ./scripts/setup_lambda_cost_alarms.sh [stage]  # default: prod

set -euo pipefail

STAGE="${1:-prod}"
REGION="sa-east-1"
FUNCTION_NAME="telecom-tower-power-${STAGE}-BatchWorkerFunction"
SNS_TOPIC_ARN=$(aws sns list-topics --region "$REGION" --query \
  "Topics[?contains(TopicArn,'telecom-tower-power')].TopicArn | [0]" --output text)

if [[ "$SNS_TOPIC_ARN" == "None" || -z "$SNS_TOPIC_ARN" ]]; then
  echo "Creating SNS topic..."
  SNS_TOPIC_ARN=$(aws sns create-topic --name telecom-tower-power-lambda-alarms \
    --region "$REGION" --query TopicArn --output text)
  echo "Created: $SNS_TOPIC_ARN"
fi

echo "Function: $FUNCTION_NAME"
echo "SNS topic: $SNS_TOPIC_ARN"
echo "Region: $REGION"
echo ""

# ── 1. High invocation rate (>500/day → consider dedicated ECS worker) ──

echo "Creating alarm: Lambda-HighInvocations-${STAGE}..."
aws cloudwatch put-metric-alarm \
  --region "$REGION" \
  --alarm-name "Lambda-HighInvocations-${STAGE}" \
  --alarm-description "Batch Lambda >500 invocations/day — review cost: switch to ECS worker?" \
  --namespace "AWS/Lambda" \
  --metric-name "Invocations" \
  --dimensions "Name=FunctionName,Value=${FUNCTION_NAME}" \
  --statistic Sum \
  --period 86400 \
  --evaluation-periods 1 \
  --threshold 500 \
  --comparison-operator GreaterThanThreshold \
  --alarm-actions "$SNS_TOPIC_ARN" \
  --treat-missing-data notBreaching

# ── 2. High average duration (>600s = 10 min avg) ────────────────────

echo "Creating alarm: Lambda-HighDuration-${STAGE}..."
aws cloudwatch put-metric-alarm \
  --region "$REGION" \
  --alarm-name "Lambda-HighDuration-${STAGE}" \
  --alarm-description "Batch Lambda avg duration >600s — approaching 15-min timeout" \
  --namespace "AWS/Lambda" \
  --metric-name "Duration" \
  --dimensions "Name=FunctionName,Value=${FUNCTION_NAME}" \
  --statistic Average \
  --period 3600 \
  --evaluation-periods 1 \
  --threshold 600000 \
  --comparison-operator GreaterThanThreshold \
  --unit Milliseconds \
  --alarm-actions "$SNS_TOPIC_ARN" \
  --treat-missing-data notBreaching

# ── 3. Throttles (any = concurrency exhaustion) ─────────────────────

echo "Creating alarm: Lambda-Throttles-${STAGE}..."
aws cloudwatch put-metric-alarm \
  --region "$REGION" \
  --alarm-name "Lambda-Throttles-${STAGE}" \
  --alarm-description "Batch Lambda throttled — increase ReservedConcurrentExecutions" \
  --namespace "AWS/Lambda" \
  --metric-name "Throttles" \
  --dimensions "Name=FunctionName,Value=${FUNCTION_NAME}" \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 1 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --alarm-actions "$SNS_TOPIC_ARN" \
  --treat-missing-data notBreaching

# ── 4. Error rate (>5%) ──────────────────────────────────────────────

echo "Creating alarm: Lambda-ErrorRate-${STAGE}..."
aws cloudwatch put-metric-alarm \
  --region "$REGION" \
  --alarm-name "Lambda-ErrorRate-${STAGE}" \
  --alarm-description "Batch Lambda error rate >5% — check DLQ and logs" \
  --namespace "AWS/Lambda" \
  --metric-name "Errors" \
  --dimensions "Name=FunctionName,Value=${FUNCTION_NAME}" \
  --statistic Average \
  --period 300 \
  --evaluation-periods 2 \
  --threshold 0.05 \
  --comparison-operator GreaterThanThreshold \
  --alarm-actions "$SNS_TOPIC_ARN" \
  --treat-missing-data notBreaching

echo ""
echo "Done. 4 alarms created for $FUNCTION_NAME."
echo ""
echo "Cost decision guide:"
echo "  - <100 invocations/day → Lambda is cheapest"
echo "  - 100–500/day → Lambda fine, monitor GB-seconds"
echo "  - >500/day → evaluate dedicated ECS worker (batch_worker.py)"
echo "  - >1000/day → dedicated ECS worker almost certainly cheaper"
echo ""
echo "To check current usage:"
echo "  aws cloudwatch get-metric-statistics --region $REGION \\"
echo "    --namespace AWS/Lambda --metric-name Invocations \\"
echo "    --dimensions Name=FunctionName,Value=$FUNCTION_NAME \\"
echo "    --start-time \$(date -d '-7 days' -u +%Y-%m-%dT%H:%M:%S) \\"
echo "    --end-time \$(date -u +%Y-%m-%dT%H:%M:%S) \\"
echo "    --period 86400 --statistics Sum"
