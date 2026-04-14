#!/usr/bin/env bash
set -euo pipefail

# Finish HTTPS setup after ACM certificate is validated.
# Usage: ./scripts/setup_https.sh

CERT_ARN="arn:aws:acm:sa-east-1:490083271496:certificate/98145af3-582e-411c-9a6d-c901de600fdf"
ALB_ARN="arn:aws:elasticloadbalancing:sa-east-1:490083271496:loadbalancer/app/telecom-tower-power-alb/f61f282ff7c0570b"
TG_ARN="arn:aws:elasticloadbalancing:sa-east-1:490083271496:targetgroup/telecom-tower-power-api-tg/745d4c4b816c7aeb"
HTTP_LISTENER="arn:aws:elasticloadbalancing:sa-east-1:490083271496:listener/app/telecom-tower-power-alb/f61f282ff7c0570b/ab7c71aa6c15eec1"
REGION="sa-east-1"

echo "=== HTTPS Setup ==="

# 1. Check certificate status
STATUS=$(aws acm describe-certificate --certificate-arn "$CERT_ARN" --region "$REGION" --query 'Certificate.Status' --output text --no-cli-pager)
echo "  Certificate status: $STATUS"
if [ "$STATUS" != "ISSUED" ]; then
  echo "  ✗ Certificate not yet validated. Add the DNS CNAME record first."
  echo "  Run: aws acm describe-certificate --certificate-arn $CERT_ARN --region $REGION --query 'Certificate.DomainValidationOptions[0].ResourceRecord' --no-cli-pager"
  exit 1
fi

# 2. Create HTTPS listener
echo "▸ Creating HTTPS listener on port 443..."
HTTPS_LISTENER=$(aws elbv2 create-listener \
  --load-balancer-arn "$ALB_ARN" \
  --protocol HTTPS --port 443 \
  --certificates CertificateArn="$CERT_ARN" \
  --ssl-policy ELBSecurityPolicy-TLS13-1-2-2021-06 \
  --default-actions "Type=forward,TargetGroupArn=$TG_ARN" \
  --region "$REGION" \
  --query 'Listeners[0].ListenerArn' --output text --no-cli-pager)
echo "  ✓ HTTPS listener: $HTTPS_LISTENER"

# 3. Redirect HTTP → HTTPS
echo "▸ Updating HTTP listener to redirect to HTTPS..."
aws elbv2 modify-listener \
  --listener-arn "$HTTP_LISTENER" \
  --default-actions 'Type=redirect,RedirectConfig={Protocol=HTTPS,Port=443,StatusCode=HTTP_301}' \
  --region "$REGION" --no-cli-pager > /dev/null
echo "  ✓ HTTP → HTTPS redirect enabled"

echo ""
echo "=== HTTPS is live ==="
echo "  https://api.telecomtowerpower.com/health"
