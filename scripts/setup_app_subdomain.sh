#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# app.telecomtowerpower.com.br DNS Setup — Route 53 A-alias → ALB
#
# Creates an A-record alias in Route 53 so that
#   app.telecomtowerpower.com.br → ALB → EC2 Caddy → React frontend
#
# The ACM wildcard certificate (*.telecomtowerpower.com.br) already covers
# this subdomain, and Caddy's :80 block already serves the React SPA for
# any traffic forwarded by the ALB.
#
# Prerequisites:
#   - setup_https.sh has already run (hosted zone + wildcard cert exist)
#   - ALB HTTPS listener is active
#
# Usage: ./scripts/setup_app_subdomain.sh
# ============================================================================

REGION="sa-east-1"
ROOT_DOMAIN="telecomtowerpower.com.br"
DOMAIN="app.telecomtowerpower.com.br"
ALB_DNS="telecom-tower-power-alb-581610578.sa-east-1.elb.amazonaws.com"
ALB_HOSTED_ZONE="Z2P70J7HTTTPLU"  # sa-east-1 ALB hosted zone (AWS-managed)

echo "=== DNS Setup for $DOMAIN ==="
echo ""

# ── 0. Look up the Route 53 hosted zone ─────────────────────────
echo "▸ Looking up Route 53 hosted zone for $ROOT_DOMAIN ..."
R53_ZONE_ID=$(aws route53 list-hosted-zones-by-name \
  --dns-name "$ROOT_DOMAIN" \
  --query "HostedZones[?Name==\`${ROOT_DOMAIN}.\`].Id" \
  --output text --no-cli-pager | sed 's|/hostedzone/||')

if [[ -z "$R53_ZONE_ID" || "$R53_ZONE_ID" == "None" ]]; then
  echo "  ✗ No hosted zone found for $ROOT_DOMAIN in Route 53."
  exit 1
fi
echo "  ✓ Hosted zone: $R53_ZONE_ID"

# ── 1. Check for existing record ────────────────────────────────
echo ""
echo "▸ Checking for existing DNS records for $DOMAIN ..."
EXISTING=$(aws route53 list-resource-record-sets \
  --hosted-zone-id "$R53_ZONE_ID" \
  --query "ResourceRecordSets[?Name==\`${DOMAIN}.\`]" \
  --output json --no-cli-pager 2>/dev/null)

RECORD_COUNT=$(echo "$EXISTING" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
if [[ "$RECORD_COUNT" -gt 0 ]]; then
  echo "  ℹ Found $RECORD_COUNT existing record(s) — will UPSERT (overwrite)."
else
  echo "  ℹ No existing record — will create new."
fi

# ── 2. Create A-record alias: app.* → ALB ───────────────────────
echo ""
echo "▸ Creating A-record alias: $DOMAIN → ALB ..."
aws route53 change-resource-record-sets \
  --hosted-zone-id "$R53_ZONE_ID" \
  --change-batch '{
    "Comment": "app subdomain → ALB for React frontend",
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

# ── 3. Verify ────────────────────────────────────────────────────
echo ""
echo "▸ Verifying DNS record ..."
aws route53 list-resource-record-sets \
  --hosted-zone-id "$R53_ZONE_ID" \
  --query "ResourceRecordSets[?Name==\`${DOMAIN}.\`]" \
  --output table --no-cli-pager 2>/dev/null || true

echo ""
echo "============================================================"
echo "  ✓ DNS record created!"
echo ""
echo "  $DOMAIN → $ALB_DNS (A-alias)"
echo "  TLS: covered by existing ACM wildcard certificate"
echo "  ALB forwards HTTP to EC2 Caddy → React SPA (port 3000)"
echo ""
echo "  DNS propagation may take a few minutes."
echo "============================================================"
echo ""
echo "Verify:"
echo "  dig $DOMAIN +short"
echo "  curl -I https://$DOMAIN"
