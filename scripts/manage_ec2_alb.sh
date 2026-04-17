#!/usr/bin/env bash
set -euo pipefail
# ============================================================================
# Manage EC2 in ALB target group (register / deregister)
#
# Usage:
#   bash scripts/manage_ec2_alb.sh deregister   # remove EC2, keep as cold standby
#   bash scripts/manage_ec2_alb.sh register      # add EC2 back to ALB rotation
#   bash scripts/manage_ec2_alb.sh status         # show current targets
# ============================================================================

AWS_REGION="${AWS_REGION:-sa-east-1}"
TARGET_GROUP_ARN="${TARGET_GROUP_ARN:-}"
EC2_TARGET_IP="${EC2_TARGET_IP:-172.31.22.201}"
EC2_TARGET_PORT="${EC2_TARGET_PORT:-8000}"

ACTION="${1:-status}"

# ── Auto-discover target group ARN if not set ────────────────────────────
if [[ -z "$TARGET_GROUP_ARN" ]]; then
  TARGET_GROUP_ARN=$(aws elbv2 describe-target-groups \
    --names telecom-tower-power-api-tg \
    --region "$AWS_REGION" \
    --query 'TargetGroups[0].TargetGroupArn' \
    --output text 2>/dev/null || echo "")
fi

if [[ -z "$TARGET_GROUP_ARN" || "$TARGET_GROUP_ARN" == "None" ]]; then
  echo "ERROR: Could not find target group. Set TARGET_GROUP_ARN." >&2
  exit 1
fi

echo "▸ Target Group: $TARGET_GROUP_ARN"
echo "▸ EC2 Target:   ${EC2_TARGET_IP}:${EC2_TARGET_PORT}"
echo ""

case "$ACTION" in
  deregister)
    echo "Deregistering EC2 from ALB (cold standby mode)..."
    aws elbv2 deregister-targets \
      --target-group-arn "$TARGET_GROUP_ARN" \
      --targets "Id=${EC2_TARGET_IP},Port=${EC2_TARGET_PORT}" \
      --region "$AWS_REGION"
    echo "✓ EC2 removed from ALB rotation. Fargate handles all traffic."
    echo "  EC2 stack stays running — re-register with: $0 register"
    ;;

  register)
    echo "Registering EC2 back into ALB..."
    aws elbv2 register-targets \
      --target-group-arn "$TARGET_GROUP_ARN" \
      --targets "Id=${EC2_TARGET_IP},Port=${EC2_TARGET_PORT}" \
      --region "$AWS_REGION"
    echo "✓ EC2 added to ALB. Traffic will route to both Fargate and EC2."
    ;;

  status)
    echo "Current targets:"
    aws elbv2 describe-target-health \
      --target-group-arn "$TARGET_GROUP_ARN" \
      --region "$AWS_REGION" \
      --output table
    ;;

  *)
    echo "Usage: $0 {register|deregister|status}" >&2
    exit 1
    ;;
esac
