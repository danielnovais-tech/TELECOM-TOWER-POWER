#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# HTTPS Setup — fully automated via AWS Route 53 + ACM + ALB
#
# DNS is managed by Route 53 (Registro.br only delegates nameservers to AWS).
# This script:
#   0. Looks up the Route 53 hosted zone
#   1. Finds or requests an ACM certificate for *.telecomtowerpower.com.br
#   2. Creates ACM validation CNAMEs in Route 53 and waits for ISSUED
#   3. Creates the api.telecomtowerpower.com.br → ALB alias record
#   4. Creates/updates the HTTPS listener on the ALB
#   5. Redirects HTTP → HTTPS
#
# Usage: ./scripts/setup_https.sh
# ============================================================================

ALB_ARN="arn:aws:elasticloadbalancing:sa-east-1:490083271496:loadbalancer/app/telecom-tower-power-alb/f61f282ff7c0570b"
TG_ARN="arn:aws:elasticloadbalancing:sa-east-1:490083271496:targetgroup/telecom-tower-power-api-tg/745d4c4b816c7aeb"
HTTP_LISTENER="arn:aws:elasticloadbalancing:sa-east-1:490083271496:listener/app/telecom-tower-power-alb/f61f282ff7c0570b/ab7c71aa6c15eec1"
REGION="sa-east-1"
DOMAIN="api.telecomtowerpower.com.br"
ROOT_DOMAIN="telecomtowerpower.com.br"
ALB_DNS="telecom-tower-power-alb-581610578.sa-east-1.elb.amazonaws.com"
ALB_HOSTED_ZONE="Z2P70J7HTTTPLU"  # sa-east-1 ALB hosted zone (AWS-managed)

echo "=== HTTPS Setup for $DOMAIN ==="
echo ""

# ── 0. Look up the Route 53 hosted zone for the root domain ─────
echo "▸ Looking up Route 53 hosted zone for $ROOT_DOMAIN ..."
R53_ZONE_ID=$(aws route53 list-hosted-zones-by-name \
  --dns-name "$ROOT_DOMAIN" \
  --query "HostedZones[?Name==\`${ROOT_DOMAIN}.\`].Id" \
  --output text --no-cli-pager | sed 's|/hostedzone/||')

if [[ -z "$R53_ZONE_ID" || "$R53_ZONE_ID" == "None" ]]; then
  echo "  ✗ No hosted zone found for $ROOT_DOMAIN in Route 53."
  echo "  Create one: aws route53 create-hosted-zone --name $ROOT_DOMAIN --caller-reference \$(date +%s)"
  exit 1
fi
echo "  ✓ Hosted zone: $R53_ZONE_ID"

# ── 1. Find or request ACM certificate for the .com.br domain ───
echo ""
echo "▸ Looking for an ACM certificate covering $DOMAIN ..."

# Search existing certificates for one that covers our domain
CERT_ARN=""
while IFS= read -r arn; do
  [[ -z "$arn" || "$arn" == "None" ]] && continue
  DOMAINS=$(aws acm describe-certificate \
    --certificate-arn "$arn" --region "$REGION" \
    --query 'Certificate.SubjectAlternativeNames' \
    --output text --no-cli-pager 2>/dev/null || true)
  # Check if any SAN matches our domain or wildcard
  for san in $DOMAINS; do
    if [[ "$san" == "$DOMAIN" || "$san" == "*.$ROOT_DOMAIN" || "$san" == "$ROOT_DOMAIN" ]]; then
      CERT_ARN="$arn"
      break 2
    fi
  done
done < <(aws acm list-certificates --region "$REGION" \
  --query 'CertificateSummaryList[*].CertificateArn' \
  --output text --no-cli-pager 2>/dev/null | tr '\t' '\n')

if [[ -z "$CERT_ARN" ]]; then
  echo "  No existing certificate found for $ROOT_DOMAIN."
  echo "▸ Requesting new ACM certificate for $ROOT_DOMAIN + *.$ROOT_DOMAIN ..."
  CERT_ARN=$(aws acm request-certificate \
    --domain-name "$ROOT_DOMAIN" \
    --subject-alternative-names "*.$ROOT_DOMAIN" \
    --validation-method DNS \
    --region "$REGION" \
    --query 'CertificateArn' --output text --no-cli-pager)
  echo "  ✓ Certificate requested: $CERT_ARN"
  echo "  Waiting 10s for ACM to generate validation records..."
  sleep 10
else
  echo "  ✓ Found certificate: $CERT_ARN"
fi

# ── 2. ACM validation CNAMEs in Route 53 ─────────────────────────
echo ""
STATUS=$(aws acm describe-certificate \
  --certificate-arn "$CERT_ARN" --region "$REGION" \
  --query 'Certificate.Status' --output text --no-cli-pager)
echo "▸ Certificate status: $STATUS"

if [ "$STATUS" != "ISSUED" ]; then
  echo ""
  echo "▸ Creating ACM validation CNAMEs in Route 53..."

  # Get all validation records (there may be multiple for domain + wildcard)
  VALIDATION_JSON=$(aws acm describe-certificate \
    --certificate-arn "$CERT_ARN" --region "$REGION" \
    --query 'Certificate.DomainValidationOptions[*].ResourceRecord.{Name:Name,Value:Value}' \
    --output json --no-cli-pager)

  # Build a single change batch with all unique validation CNAMEs
  CHANGES="[]"
  declare -A SEEN_NAMES=()
  while IFS= read -r entry; do
    VNAME=$(echo "$entry" | python3 -c "import sys,json; print(json.load(sys.stdin)['Name'])")
    VVALUE=$(echo "$entry" | python3 -c "import sys,json; print(json.load(sys.stdin)['Value'])")

    # Skip if we already processed this name (wildcard + base often share the same CNAME)
    [[ -n "${SEEN_NAMES[$VNAME]:-}" ]] && continue
    SEEN_NAMES["$VNAME"]=1

    # Only create records that belong to our .com.br zone
    if [[ "$VNAME" == *"${ROOT_DOMAIN}."* || "$VNAME" == *"${ROOT_DOMAIN}" ]]; then
      echo "  CNAME: $VNAME → $VVALUE"
      CHANGES=$(echo "$CHANGES" | python3 -c "
import sys, json
changes = json.load(sys.stdin)
changes.append({
    'Action': 'UPSERT',
    'ResourceRecordSet': {
        'Name': '$VNAME',
        'Type': 'CNAME',
        'TTL': 300,
        'ResourceRecords': [{'Value': '$VVALUE'}]
    }
})
print(json.dumps(changes))
")
    else
      echo "  ⚠ Skipping $VNAME (does not belong to $ROOT_DOMAIN zone)"
    fi
  done < <(echo "$VALIDATION_JSON" | python3 -c "
import sys, json
for item in json.load(sys.stdin):
    if item.get('Name') and item.get('Value'):
        print(json.dumps(item))
")

  NUM_CHANGES=$(echo "$CHANGES" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
  if [[ "$NUM_CHANGES" -gt 0 ]]; then
    aws route53 change-resource-record-sets \
      --hosted-zone-id "$R53_ZONE_ID" \
      --change-batch "{\"Changes\": $CHANGES}" \
      --no-cli-pager > /dev/null
    echo "  ✓ $NUM_CHANGES validation CNAME(s) created in Route 53"
  else
    echo "  ✗ No validation CNAMEs matched the $ROOT_DOMAIN zone."
    echo "    The certificate may cover a different domain."
    echo "    Check: aws acm describe-certificate --certificate-arn $CERT_ARN --region $REGION --no-cli-pager"
    exit 1
  fi

  echo ""
  echo "▸ Waiting for certificate validation (this may take a few minutes)..."
  aws acm wait certificate-validated \
    --certificate-arn "$CERT_ARN" --region "$REGION" 2>/dev/null \
    || true

  # Re-check status
  STATUS=$(aws acm describe-certificate \
    --certificate-arn "$CERT_ARN" --region "$REGION" \
    --query 'Certificate.Status' --output text --no-cli-pager)
  echo "  Certificate status: $STATUS"
  if [ "$STATUS" != "ISSUED" ]; then
    echo "  ✗ Still not validated. DNS propagation may take up to 30 minutes."
    echo "  Re-run this script once the status is ISSUED."
    exit 1
  fi
fi
echo "  ✓ Certificate is ISSUED"

# ── 2. Create api.telecomtowerpower.com.br → ALB alias in Route 53 ──
echo ""
echo "▸ Creating A-record alias: $DOMAIN → ALB..."
aws route53 change-resource-record-sets \
  --hosted-zone-id "$R53_ZONE_ID" \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "'"$DOMAIN"'",
        "Type": "A",
        "AliasTarget": {
          "HostedZoneId": "'"$ALB_HOSTED_ZONE"'",
          "DNSName": "'"$ALB_DNS"'",
          "EvaluateTargetHealth": true
        }
      }
    }]
  }' --no-cli-pager > /dev/null
echo "  ✓ $DOMAIN → $ALB_DNS"

# ── 3. HTTPS listener on ALB ────────────────────────────────────
echo ""
EXISTING_443=$(aws elbv2 describe-listeners \
  --load-balancer-arn "$ALB_ARN" --region "$REGION" \
  --query "Listeners[?Port==\`443\`].ListenerArn" \
  --output text --no-cli-pager 2>/dev/null || true)

if [[ -n "$EXISTING_443" && "$EXISTING_443" != "None" ]]; then
  echo "▸ HTTPS listener already exists, updating certificate..."
  aws elbv2 modify-listener \
    --listener-arn "$EXISTING_443" \
    --certificates CertificateArn="$CERT_ARN" \
    --ssl-policy ELBSecurityPolicy-TLS13-1-2-2021-06 \
    --region "$REGION" --no-cli-pager > /dev/null
  echo "  ✓ HTTPS listener updated: $EXISTING_443"
else
  echo "▸ Creating HTTPS listener on port 443..."
  HTTPS_LISTENER=$(aws elbv2 create-listener \
    --load-balancer-arn "$ALB_ARN" \
    --protocol HTTPS --port 443 \
    --certificates CertificateArn="$CERT_ARN" \
    --ssl-policy ELBSecurityPolicy-TLS13-1-2-2021-06 \
    --default-actions "Type=forward,TargetGroupArn=$TG_ARN" \
    --region "$REGION" \
    --query 'Listeners[0].ListenerArn' --output text --no-cli-pager)
  echo "  ✓ HTTPS listener created: $HTTPS_LISTENER"
fi

# ── 4. HTTP → HTTPS redirect ────────────────────────────────────
echo ""
echo "▸ Setting HTTP → HTTPS redirect (301)..."
aws elbv2 modify-listener \
  --listener-arn "$HTTP_LISTENER" \
  --default-actions 'Type=redirect,RedirectConfig={Protocol=HTTPS,Port=443,StatusCode=HTTP_301}' \
  --region "$REGION" --no-cli-pager > /dev/null
echo "  ✓ HTTP → HTTPS redirect enabled"

# ── 5. Done ──────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  HTTPS is live!"
echo "  https://$DOMAIN/health"
echo ""
echo "  DNS:  $DOMAIN → $ALB_DNS (Route 53 alias)"
echo "  TLS:  ACM certificate $CERT_ARN"
echo "  HSTS: Strict-Transport-Security header set in API"
echo "============================================================"
echo ""
echo "Verify: curl -I https://$DOMAIN/health"
