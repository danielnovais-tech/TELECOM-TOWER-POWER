#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Monitoring Subdomain Setup — Grafana & Prometheus via ALB + Route 53
#
# Creates:
#   monitoring.telecomtowerpower.com.br → Grafana  (port 3001 on EC2)
#   prometheus.telecomtowerpower.com.br → Prometheus (port 9090 on EC2)
#
# Prerequisites:
#   - ACM wildcard certificate (*.telecomtowerpower.com.br) already issued
#   - ALB + HTTPS listener already created (via setup_https.sh)
#   - Grafana + Prometheus running in Docker on the EC2 instance
#
# The script:
#   1. Creates ALB target groups for Grafana and Prometheus
#   2. Registers the EC2 instance in both target groups
#   3. Adds host-based routing rules on the HTTPS listener
#   4. Creates Route 53 A-record aliases for the subdomains
#
# Usage: ./scripts/setup_monitoring_subdomains.sh
# ============================================================================

ALB_ARN="arn:aws:elasticloadbalancing:sa-east-1:490083271496:loadbalancer/app/telecom-tower-power-alb/f61f282ff7c0570b"
REGION="sa-east-1"
ROOT_DOMAIN="telecomtowerpower.com.br"
ALB_DNS="telecom-tower-power-alb-581610578.sa-east-1.elb.amazonaws.com"
ALB_HOSTED_ZONE="Z2P70J7HTTTPLU"  # sa-east-1 ALB hosted zone (AWS-managed)

# VPC ID — same VPC as the ALB
VPC_ID=$(aws elbv2 describe-load-balancers \
  --load-balancer-arns "$ALB_ARN" --region "$REGION" \
  --query 'LoadBalancers[0].VpcId' --output text --no-cli-pager)

echo "=== Monitoring Subdomain Setup ==="
echo "  VPC: $VPC_ID"
echo ""

# ── 0. Look up Route 53 hosted zone ─────────────────────────────
R53_ZONE_ID=$(aws route53 list-hosted-zones-by-name \
  --dns-name "$ROOT_DOMAIN" \
  --query "HostedZones[?Name==\`${ROOT_DOMAIN}.\`].Id" \
  --output text --no-cli-pager | sed 's|/hostedzone/||')

if [[ -z "$R53_ZONE_ID" || "$R53_ZONE_ID" == "None" ]]; then
  echo "✗ No hosted zone found for $ROOT_DOMAIN"
  exit 1
fi
echo "▸ Route 53 zone: $R53_ZONE_ID"

# ── 1. Find the HTTPS listener ──────────────────────────────────
HTTPS_LISTENER=$(aws elbv2 describe-listeners \
  --load-balancer-arn "$ALB_ARN" --region "$REGION" \
  --query "Listeners[?Port==\`443\`].ListenerArn" \
  --output text --no-cli-pager)

if [[ -z "$HTTPS_LISTENER" || "$HTTPS_LISTENER" == "None" ]]; then
  echo "✗ No HTTPS listener found on ALB. Run setup_https.sh first."
  exit 1
fi
echo "▸ HTTPS listener: $HTTPS_LISTENER"

# ── 2. Get EC2 instance ID (via existing API target group) ──────
API_TG_ARN="arn:aws:elasticloadbalancing:sa-east-1:490083271496:targetgroup/telecom-tower-power-api-tg/745d4c4b816c7aeb"
INSTANCE_ID=$(aws elbv2 describe-target-health \
  --target-group-arn "$API_TG_ARN" --region "$REGION" \
  --query 'TargetHealthDescriptions[0].Target.Id' \
  --output text --no-cli-pager)

if [[ -z "$INSTANCE_ID" || "$INSTANCE_ID" == "None" ]]; then
  echo "✗ No EC2 instance found in API target group"
  exit 1
fi
echo "▸ EC2 instance: $INSTANCE_ID"
echo ""

# ── Helper: create target group + register instance + add rule ──
setup_subdomain() {
  local NAME="$1"      # e.g. "grafana"
  local SUBDOMAIN="$2" # e.g. "monitoring.telecomtowerpower.com.br"
  local PORT="$3"      # e.g. 3001
  local PRIORITY="$4"  # listener rule priority (must be unique)
  local HEALTH_PATH="$5"

  local TG_NAME="ttp-${NAME}-tg"
  echo "── Setting up $SUBDOMAIN → port $PORT ──"

  # Create target group (or find existing)
  TG_ARN=$(aws elbv2 describe-target-groups \
    --names "$TG_NAME" --region "$REGION" \
    --query 'TargetGroups[0].TargetGroupArn' \
    --output text --no-cli-pager 2>/dev/null || echo "")

  if [[ -z "$TG_ARN" || "$TG_ARN" == "None" ]]; then
    echo "  ▸ Creating target group: $TG_NAME (port $PORT)..."
    TG_ARN=$(aws elbv2 create-target-group \
      --name "$TG_NAME" \
      --protocol HTTP --port "$PORT" \
      --vpc-id "$VPC_ID" \
      --target-type instance \
      --health-check-path "$HEALTH_PATH" \
      --health-check-interval-seconds 30 \
      --healthy-threshold-count 2 \
      --unhealthy-threshold-count 3 \
      --region "$REGION" \
      --query 'TargetGroups[0].TargetGroupArn' \
      --output text --no-cli-pager)
    echo "  ✓ Target group created: $TG_ARN"
  else
    echo "  ✓ Target group exists: $TG_ARN"
  fi

  # Register EC2 instance
  echo "  ▸ Registering instance $INSTANCE_ID (port $PORT)..."
  aws elbv2 register-targets \
    --target-group-arn "$TG_ARN" \
    --targets "Id=$INSTANCE_ID,Port=$PORT" \
    --region "$REGION" --no-cli-pager 2>/dev/null || true
  echo "  ✓ Instance registered"

  # Add host-based routing rule on HTTPS listener
  # Check if rule already exists for this host
  EXISTING_RULE=$(aws elbv2 describe-rules \
    --listener-arn "$HTTPS_LISTENER" --region "$REGION" \
    --query "Rules[?Conditions[?Values[0]==\`$SUBDOMAIN\`]].RuleArn" \
    --output text --no-cli-pager 2>/dev/null || echo "")

  if [[ -z "$EXISTING_RULE" || "$EXISTING_RULE" == "None" ]]; then
    echo "  ▸ Creating listener rule (priority $PRIORITY): Host=$SUBDOMAIN..."
    aws elbv2 create-rule \
      --listener-arn "$HTTPS_LISTENER" \
      --priority "$PRIORITY" \
      --conditions "Field=host-header,Values=$SUBDOMAIN" \
      --actions "Type=forward,TargetGroupArn=$TG_ARN" \
      --region "$REGION" --no-cli-pager > /dev/null
    echo "  ✓ Listener rule created"
  else
    echo "  ✓ Listener rule already exists"
  fi

  # Create Route 53 A-record alias
  echo "  ▸ Creating DNS alias: $SUBDOMAIN → ALB..."
  aws route53 change-resource-record-sets \
    --hosted-zone-id "$R53_ZONE_ID" \
    --change-batch '{
      "Changes": [{
        "Action": "UPSERT",
        "ResourceRecordSet": {
          "Name": "'"$SUBDOMAIN"'",
          "Type": "A",
          "AliasTarget": {
            "HostedZoneId": "'"$ALB_HOSTED_ZONE"'",
            "DNSName": "'"$ALB_DNS"'",
            "EvaluateTargetHealth": true
          }
        }
      }]
    }' --no-cli-pager > /dev/null
  echo "  ✓ DNS alias created: $SUBDOMAIN → $ALB_DNS"
  echo ""
}

# ── 3. Set up Grafana subdomain ──────────────────────────────────
setup_subdomain "grafana" "monitoring.$ROOT_DOMAIN" 3001 10 "/api/health"

# ── 4. Set up Prometheus subdomain ───────────────────────────────
setup_subdomain "prometheus" "prometheus.$ROOT_DOMAIN" 9090 11 "/-/healthy"

# ── 5. Security: update EC2 security group ───────────────────────
echo "── Security Group Check ──"
echo "  Ensure EC2 security group allows inbound from ALB on ports 3001 and 9090."
echo "  If not already open, run:"
echo ""
echo "    SG_ID=\$(aws ec2 describe-instances --instance-ids $INSTANCE_ID \\"
echo "      --query 'Reservations[0].Instances[0].SecurityGroups[0].GroupId' \\"
echo "      --output text --region $REGION)"
echo ""
echo "    aws ec2 authorize-security-group-ingress --group-id \$SG_ID \\"
echo "      --protocol tcp --port 3001 --source-group <ALB-SG-ID> --region $REGION"
echo ""
echo "    aws ec2 authorize-security-group-ingress --group-id \$SG_ID \\"
echo "      --protocol tcp --port 9090 --source-group <ALB-SG-ID> --region $REGION"
echo ""

# ── 6. Done ──────────────────────────────────────────────────────
echo "============================================================"
echo "  Monitoring subdomains configured!"
echo ""
echo "  Grafana:    https://monitoring.$ROOT_DOMAIN"
echo "  Prometheus: https://prometheus.$ROOT_DOMAIN"
echo ""
echo "  Both use the existing ACM wildcard certificate."
echo "  DNS propagation may take a few minutes."
echo "============================================================"
echo ""
echo "Verify:"
echo "  curl -I https://monitoring.$ROOT_DOMAIN/api/health"
echo "  curl -I https://prometheus.$ROOT_DOMAIN/-/healthy"
