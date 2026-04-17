#!/usr/bin/env bash
set -euo pipefail
# ============================================================================
# Create CloudWatch alarms for the EC2 instance running the self-contained
# Docker Compose stack.  Alerts go to an SNS topic that fans out to Slack
# and email.
#
# Usage:  bash scripts/setup_ec2_alarms.sh
#
# Prerequisites:
#   - AWS CLI configured with sa-east-1 region
#   - EC2 instance ID (auto-detected or set EC2_INSTANCE_ID)
# ============================================================================

AWS_REGION="${AWS_REGION:-sa-east-1}"
INSTANCE_ID="${EC2_INSTANCE_ID:-i-045166a6a1933f507}"
SNS_TOPIC_NAME="telecom-tower-power-ec2-alarms"
ALARM_PREFIX="telecom-tower-power-ec2"
ALERT_EMAIL="${ALERT_EMAIL:-daniel.novais@sempreceub.com}"

echo "▸ Region:   $AWS_REGION"
echo "▸ Instance: $INSTANCE_ID"

# ── Step 1: Create / reuse SNS topic ────────────────────────────────────
TOPIC_ARN=$(aws sns create-topic \
  --name "$SNS_TOPIC_NAME" \
  --region "$AWS_REGION" \
  --query 'TopicArn' --output text)
echo "▸ SNS topic: $TOPIC_ARN"

# Subscribe email (idempotent — AWS deduplicates)
aws sns subscribe \
  --topic-arn "$TOPIC_ARN" \
  --protocol email \
  --notification-endpoint "$ALERT_EMAIL" \
  --region "$AWS_REGION" >/dev/null 2>&1 || true
echo "▸ Email subscription: $ALERT_EMAIL (confirm via inbox if new)"

# ── Step 2: StatusCheckFailed alarm ──────────────────────────────────────
# Fires if either the instance or system status check fails for 2 consecutive
# 1-minute periods.  Detects hardware issues, network loss, host failures.
aws cloudwatch put-metric-alarm \
  --alarm-name "${ALARM_PREFIX}-status-check-failed" \
  --alarm-description "EC2 instance or system status check failed" \
  --namespace AWS/EC2 \
  --metric-name StatusCheckFailed \
  --dimensions "Name=InstanceId,Value=${INSTANCE_ID}" \
  --statistic Maximum \
  --period 60 \
  --evaluation-periods 2 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --alarm-actions "$TOPIC_ARN" \
  --ok-actions "$TOPIC_ARN" \
  --treat-missing-data breaching \
  --region "$AWS_REGION"
echo "✓ StatusCheckFailed alarm created"

# ── Step 3: High CPU alarm ───────────────────────────────────────────────
# t3.micro can burst but sustained >85% likely means trouble on 1 GB RAM.
aws cloudwatch put-metric-alarm \
  --alarm-name "${ALARM_PREFIX}-high-cpu" \
  --alarm-description "EC2 CPU utilization above 85% for 10 minutes" \
  --namespace AWS/EC2 \
  --metric-name CPUUtilization \
  --dimensions "Name=InstanceId,Value=${INSTANCE_ID}" \
  --statistic Average \
  --period 300 \
  --evaluation-periods 2 \
  --threshold 85 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --alarm-actions "$TOPIC_ARN" \
  --ok-actions "$TOPIC_ARN" \
  --treat-missing-data missing \
  --region "$AWS_REGION"
echo "✓ HighCPU alarm created"

# ── Step 4: Disk space alarm (requires CW Agent, skip if not installed) ──
# This is a placeholder — requires the CloudWatch agent to publish
# disk_used_percent.  Uncomment after installing CW Agent on the instance.
# aws cloudwatch put-metric-alarm \
#   --alarm-name "${ALARM_PREFIX}-disk-space" \
#   --alarm-description "EC2 root volume >90% full" \
#   --namespace CWAgent \
#   --metric-name disk_used_percent \
#   --dimensions "Name=InstanceId,Value=${INSTANCE_ID}" "Name=path,Value=/" "Name=fstype,Value=ext4" \
#   --statistic Average \
#   --period 300 \
#   --evaluation-periods 2 \
#   --threshold 90 \
#   --comparison-operator GreaterThanOrEqualToThreshold \
#   --alarm-actions "$TOPIC_ARN" \
#   --ok-actions "$TOPIC_ARN" \
#   --region "$AWS_REGION"
# echo "✓ DiskSpace alarm created"

echo ""
echo "Done. Alarms will fire to SNS topic: $TOPIC_ARN"
echo "Verify in: https://${AWS_REGION}.console.aws.amazon.com/cloudwatch/home?region=${AWS_REGION}#alarmsV2:"
