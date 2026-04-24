#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Route 53 Health-Check Failover — DNS Failover to Railway
#
# Creates a Route 53 health check monitoring the ALB endpoint, then sets up
# failover DNS records for api.telecomtowerpower.com.br:
#   PRIMARY   → ALB (with health check)
#   SECONDARY → Railway (fallback when ALB is unhealthy)
#
# The script:
#   0. Looks up the Route 53 hosted zone
#   1. Creates (or reuses) a health check monitoring the ALB via HTTPS
#   2. Deletes the existing simple A-alias record for api.*
#   3. Creates CNAME failover records (PRIMARY → ALB, SECONDARY → Railway)
#   4. Verifies health check status and DNS records
#
# Prerequisites:
#   - setup_https.sh has already run (hosted zone + certs exist)
#   - AWS CLI configured with route53:* permissions
#   - Railway custom domain configured for api.telecomtowerpower.com.br
#     (so Railway's SSL cert covers the domain during failover)
#
# Usage: ./scripts/setup_failover.sh
# ============================================================================

ROOT_DOMAIN="telecomtowerpower.com.br"
DOMAIN="api.telecomtowerpower.com.br"
ALB_DNS="telecom-tower-power-alb-581610578.sa-east-1.elb.amazonaws.com"
ALB_HOSTED_ZONE="Z2P70J7HTTTPLU"  # sa-east-1 ALB hosted zone (AWS-managed)
# Railway custom-domain edge target (unique per custom domain; NOT the default
# web-production-*.up.railway.app which only serves the fallback wildcard cert).
# Retrieve from Railway UI → service web → Settings → Networking → Show DNS records.
RAILWAY_DNS="i1fuknjg.up.railway.app"
HEALTH_CHECK_PATH="/health"
FAILOVER_TTL=60  # Low TTL for fast failover

echo "=== Route 53 Failover Setup for $DOMAIN ==="
echo ""

# ── 0. Look up the Route 53 hosted zone ─────────────────────────
echo "▸ Looking up Route 53 hosted zone for $ROOT_DOMAIN ..."
R53_ZONE_ID=$(aws route53 list-hosted-zones-by-name \
  --dns-name "$ROOT_DOMAIN" \
  --query "HostedZones[?Name==\`${ROOT_DOMAIN}.\`].Id" \
  --output text --no-cli-pager | sed 's|/hostedzone/||')

if [[ -z "$R53_ZONE_ID" || "$R53_ZONE_ID" == "None" ]]; then
  echo "  ✗ No hosted zone found for $ROOT_DOMAIN"
  exit 1
fi
echo "  ✓ Hosted zone: $R53_ZONE_ID"

# ── 1. Create (or reuse) Route 53 health check ──────────────────
echo ""
echo "▸ Checking for existing health check on $ALB_DNS$HEALTH_CHECK_PATH ..."

# Look for an existing health check that matches our config
EXISTING_HC=""
while IFS= read -r hc_id; do
  [[ -z "$hc_id" || "$hc_id" == "None" ]] && continue
  HC_FQDN=$(aws route53 get-health-check \
    --health-check-id "$hc_id" \
    --query 'HealthCheck.HealthCheckConfig.FullyQualifiedDomainName' \
    --output text --no-cli-pager 2>/dev/null || true)
  HC_PATH=$(aws route53 get-health-check \
    --health-check-id "$hc_id" \
    --query 'HealthCheck.HealthCheckConfig.ResourcePath' \
    --output text --no-cli-pager 2>/dev/null || true)
  if [[ "$HC_FQDN" == "$ALB_DNS" && "$HC_PATH" == "$HEALTH_CHECK_PATH" ]]; then
    EXISTING_HC="$hc_id"
    break
  fi
done < <(aws route53 list-health-checks \
  --query 'HealthChecks[*].Id' \
  --output text --no-cli-pager 2>/dev/null | tr '\t' '\n')

if [[ -n "$EXISTING_HC" ]]; then
  HC_ID="$EXISTING_HC"
  echo "  ✓ Reusing existing health check: $HC_ID"
else
  echo "▸ Creating HTTPS health check for $ALB_DNS$HEALTH_CHECK_PATH ..."
  HC_ID=$(aws route53 create-health-check \
    --caller-reference "failover-${DOMAIN}-$(date +%s)" \
    --health-check-config '{
      "Type": "HTTPS_STR_MATCH",
      "FullyQualifiedDomainName": "'"$ALB_DNS"'",
      "Port": 443,
      "ResourcePath": "'"$HEALTH_CHECK_PATH"'",
      "SearchString": "healthy",
      "RequestInterval": 30,
      "FailureThreshold": 3,
      "MeasureLatency": true,
      "EnableSNI": true
    }' \
    --query 'HealthCheck.Id' --output text --no-cli-pager)
  echo "  ✓ Health check created: $HC_ID"

  # Tag the health check for easy identification in the console
  aws route53 change-tags-for-resource \
    --resource-type healthcheck \
    --resource-id "$HC_ID" \
    --add-tags Key=Name,Value="$DOMAIN - ALB failover" \
    --no-cli-pager 2>/dev/null || true
  echo "  ✓ Tagged health check: $DOMAIN - ALB failover"
fi

# ── 2. Remove existing simple A-alias record ─────────────────────
echo ""
echo "▸ Checking for existing simple A-alias record for $DOMAIN ..."

# Fetch all A records for the domain
EXISTING_RECORDS=$(aws route53 list-resource-record-sets \
  --hosted-zone-id "$R53_ZONE_ID" \
  --query "ResourceRecordSets[?Name==\`${DOMAIN}.\` && Type==\`A\`]" \
  --output json --no-cli-pager 2>/dev/null)

# Count simple (non-failover) A records
SIMPLE_COUNT=$(echo "$EXISTING_RECORDS" | python3 -c "
import sys, json
records = json.load(sys.stdin)
simple = [r for r in records if 'SetIdentifier' not in r]
print(len(simple))
")

if [[ "$SIMPLE_COUNT" -gt 0 ]]; then
  echo "  Found $SIMPLE_COUNT simple A-alias record(s), deleting..."
  aws route53 change-resource-record-sets \
    --hosted-zone-id "$R53_ZONE_ID" \
    --change-batch '{
      "Changes": [{
        "Action": "DELETE",
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
  echo "  ✓ Simple A-alias record deleted"
else
  echo "  ℹ No simple A-alias record found (may already be failover-configured)"
fi

# ── 3. Create failover CNAME record set ──────────────────────────
echo ""
echo "▸ Creating failover CNAME records for $DOMAIN ..."
echo "    PRIMARY   → $ALB_DNS (health-checked)"
echo "    SECONDARY → $RAILWAY_DNS (fallback)"
echo "    TTL       → ${FAILOVER_TTL}s"

aws route53 change-resource-record-sets \
  --hosted-zone-id "$R53_ZONE_ID" \
  --change-batch '{
    "Comment": "Failover: ALB primary, Railway secondary for '"$DOMAIN"'",
    "Changes": [
      {
        "Action": "UPSERT",
        "ResourceRecordSet": {
          "Name": "'"$DOMAIN"'",
          "Type": "CNAME",
          "SetIdentifier": "primary-alb",
          "Failover": "PRIMARY",
          "TTL": '"$FAILOVER_TTL"',
          "ResourceRecords": [{"Value": "'"$ALB_DNS"'"}],
          "HealthCheckId": "'"$HC_ID"'"
        }
      },
      {
        "Action": "UPSERT",
        "ResourceRecordSet": {
          "Name": "'"$DOMAIN"'",
          "Type": "CNAME",
          "SetIdentifier": "secondary-railway",
          "Failover": "SECONDARY",
          "TTL": '"$FAILOVER_TTL"',
          "ResourceRecords": [{"Value": "'"$RAILWAY_DNS"'"}]
        }
      }
    ]
  }' --no-cli-pager > /dev/null

echo "  ✓ Failover CNAME records created"

# ── 4. Verify ─────────────────────────────────────────────────────
echo ""
echo "▸ Verifying health check status (may take 30-60s to initialize)..."
HC_STATUS=$(aws route53 get-health-check-status \
  --health-check-id "$HC_ID" \
  --query 'HealthCheckObservations[0].StatusReport.Status' \
  --output text --no-cli-pager 2>/dev/null || echo "INITIALIZING")
echo "  Health check: $HC_STATUS"

echo ""
echo "▸ DNS records for $DOMAIN:"
aws route53 list-resource-record-sets \
  --hosted-zone-id "$R53_ZONE_ID" \
  --query "ResourceRecordSets[?Name==\`${DOMAIN}.\`]" \
  --output table --no-cli-pager 2>/dev/null || true

echo ""
echo "============================================================"
echo "  ✓ Failover DNS configured!"
echo ""
echo "  PRIMARY:      $DOMAIN → $ALB_DNS"
echo "  SECONDARY:    $DOMAIN → $RAILWAY_DNS"
echo "  Health check: https://$ALB_DNS$HEALTH_CHECK_PATH"
echo "  HC ID:        $HC_ID"
echo "  Hosted zone:  $R53_ZONE_ID"
echo ""
echo "  ⚠  Railway free tier has 30-60s cold-start delay."
echo "     Consider upgrading to a paid plan for always-on."
echo ""
echo "  ⚠  Ensure Railway has a custom domain configured for"
echo "     $DOMAIN so its SSL cert covers the domain."
echo "     (railway domain $DOMAIN)"
echo "============================================================"
echo ""
echo "Useful commands:"
echo "  # Check health check status"
echo "  aws route53 get-health-check-status --health-check-id $HC_ID --no-cli-pager"
echo ""
echo "  # Verify DNS resolution"
echo "  dig $DOMAIN +short"
echo ""
echo "  # Test endpoint"
echo "  curl -sI https://$DOMAIN/health"
