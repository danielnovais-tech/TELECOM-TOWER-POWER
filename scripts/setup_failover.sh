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
# Railway may rotate this value — always re-verify in the Railway UI
# (service web → Settings → Networking → Show DNS records) before running this
# script. Override at runtime:  RAILWAY_DNS=xxxx.up.railway.app ./setup_failover.sh
RAILWAY_DNS="${RAILWAY_DNS:-web-production-90b1f.up.railway.app}"
# Companion TXT record Railway uses to validate ownership of the custom domain.
# If this record is removed, Railway will stop serving a valid TLS cert for
# $DOMAIN and the SECONDARY leg of the failover will break with TLS errors.
RAILWAY_TXT_NAME="_railway-verify.api.${ROOT_DOMAIN}"
HEALTH_CHECK_PATH="/health"
FAILOVER_TTL=60  # Low TTL for fast failover

# CLI flags:
#   --skip-preflight : bypass the Railway target preflight checks
#                      (use only if you know the edge + cert are healthy
#                       but preflight cannot confirm it — e.g. network
#                       restrictions on the host running this script).
#   --force          : implies --skip-preflight AND auto-confirms a SECONDARY
#                      CNAME value change (for CI/runbook non-interactive use).
#   --yes / -y       : auto-confirm a SECONDARY CNAME value change only
#                      (still runs preflight).
SKIP_PREFLIGHT=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --force)          SKIP_PREFLIGHT=1; ASSUME_YES=1 ;;
    --skip-preflight) SKIP_PREFLIGHT=1 ;;
    --yes|-y)         ASSUME_YES=1 ;;
    -h|--help)
      sed -n '1,40p' "$0"; exit 0 ;;
  esac
done

echo "=== Route 53 Failover Setup for $DOMAIN ==="
echo ""
echo "  Railway edge target: $RAILWAY_DNS"
echo "  Railway TXT record : $RAILWAY_TXT_NAME (must remain in Route 53)"
echo ""

# ── Preflight: validate Railway target before touching DNS ──────
# Addresses risks of:
#  - stale/rotated RAILWAY_DNS silently being re-applied
#  - missing _railway-verify TXT ownership record (TLS would fail on failover)
#  - Railway not serving a cert for $DOMAIN on the edge we're about to pin
preflight_railway() {
  local ok=1

  echo "▸ Preflight: checking Railway edge resolves ..."
  if ! getent hosts "$RAILWAY_DNS" >/dev/null 2>&1 \
      && ! host "$RAILWAY_DNS" >/dev/null 2>&1; then
    echo "  ✗ Cannot resolve $RAILWAY_DNS — refusing to pin a dead edge in Route 53."
    ok=0
  else
    echo "  ✓ $RAILWAY_DNS resolves"
  fi

  echo "▸ Preflight: checking Railway serves a cert valid for $DOMAIN ..."
  # SNI-based connect: ask the Railway edge to present the cert for $DOMAIN.
  # If Railway is not configured for the custom domain (or TXT validation
  # lapsed), this will either fail or return a cert whose CN/SAN does not match.
  if command -v openssl >/dev/null 2>&1; then
    local cert_info
    cert_info=$(echo | timeout 10 openssl s_client \
        -connect "${RAILWAY_DNS}:443" \
        -servername "$DOMAIN" \
        2>/dev/null | openssl x509 -noout -text 2>/dev/null || true)
    if echo "$cert_info" | grep -Eq "DNS:${DOMAIN}([^A-Za-z0-9.-]|$)|CN *= *${DOMAIN}"; then
      echo "  ✓ Railway edge presents a cert covering $DOMAIN"
    else
      echo "  ✗ Railway edge did NOT present a cert covering $DOMAIN."
      echo "    This usually means:"
      echo "      - the custom domain is not attached in Railway, OR"
      echo "      - the $RAILWAY_TXT_NAME TXT record is missing / wrong, OR"
      echo "      - Railway rotated the edge and \$RAILWAY_DNS is stale."
      ok=0
    fi
  else
    echo "  ⚠ openssl not available — skipping TLS cert preflight"
  fi

  echo "▸ Preflight: checking $RAILWAY_TXT_NAME TXT record in Route 53 ..."
  local txt_count
  txt_count=$(aws route53 list-resource-record-sets \
    --hosted-zone-id "$R53_ZONE_ID" \
    --query "ResourceRecordSets[?Name==\`${RAILWAY_TXT_NAME}.\` && Type==\`TXT\`] | length(@)" \
    --output text --no-cli-pager 2>/dev/null || echo 0)
  if [[ "$txt_count" =~ ^[0-9]+$ ]] && (( txt_count > 0 )); then
    echo "  ✓ TXT $RAILWAY_TXT_NAME present ($txt_count record set(s))"
  else
    echo "  ✗ TXT $RAILWAY_TXT_NAME is MISSING in Route 53."
    echo "    Railway needs this record to keep serving a valid cert for $DOMAIN."
    echo "    Re-add the value shown in Railway UI → Settings → Networking"
    echo "    BEFORE proceeding, otherwise the SECONDARY leg will fail TLS."
    ok=0
  fi

  if (( ok == 0 )); then
    if (( SKIP_PREFLIGHT == 1 )); then
      echo ""
      echo "  ⚠ Preflight failed but --force/--skip-preflight was passed — continuing anyway."
    else
      echo ""
      echo "  Refusing to modify Route 53 failover records. Fix the issues above"
      echo "  or re-run with --force if you have out-of-band confirmation."
      exit 2
    fi
  fi
}

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

# Run Railway-target preflight now that we know the zone id
preflight_railway

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

# ── 3a. Guard against accidental SECONDARY value change ─────────
# If a SECONDARY failover CNAME already exists and its value differs from
# $RAILWAY_DNS, require explicit confirmation before overwriting it. This
# catches the common mistake of running the script with a test/example value
# of RAILWAY_DNS (e.g. "newedge.up.railway.app") and silently pointing the
# DR leg at a bogus host.
CURRENT_SECONDARY=$(aws route53 list-resource-record-sets \
  --hosted-zone-id "$R53_ZONE_ID" \
  --query "ResourceRecordSets[?Name==\`${DOMAIN}.\` && Type==\`CNAME\` && SetIdentifier==\`secondary-railway\`].ResourceRecords[0].Value | [0]" \
  --output text --no-cli-pager 2>/dev/null || echo "")
if [[ -n "$CURRENT_SECONDARY" && "$CURRENT_SECONDARY" != "None" && "$CURRENT_SECONDARY" != "$RAILWAY_DNS" ]]; then
  echo ""
  echo "  ⚠  SECONDARY CNAME value is CHANGING:"
  echo "       current : $CURRENT_SECONDARY"
  echo "       new     : $RAILWAY_DNS"
  if (( ASSUME_YES == 1 )); then
    echo "     --force/--yes set, continuing without prompt."
  elif [[ ! -t 0 ]]; then
    echo "  ✗ Refusing to change SECONDARY non-interactively without --yes/--force."
    echo "    Re-run with --yes if this change is intentional."
    exit 3
  else
    printf '     Type "yes" to confirm this change: '
    read -r CONFIRM
    if [[ "$CONFIRM" != "yes" ]]; then
      echo "  ✗ Aborted by user. SECONDARY unchanged."
      exit 3
    fi
  fi
fi

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
echo "  ⚠  The Route 53 health check only measures the ALB (PRIMARY)."
echo "     Route 53 will still flip to Railway even if Railway is unhealthy."
echo "     Run  scripts/verify_failover.sh  on a schedule to detect a sick"
echo "     SECONDARY (stale edge, missing TXT, cert mismatch) BEFORE an incident."
echo ""
echo "  ⚠  Railway cold-start consideration: on free/hobby tier, the first"
echo "     request after idle can take 30-60s. If this is your DR target,"
echo "     run Railway on an always-on plan, otherwise expect that additional"
echo "     latency on the first requests after Route 53 flips."
echo ""
echo "  ⚠  Ensure Railway has a custom domain configured for"
echo "     $DOMAIN so its SSL cert covers the domain."
echo "     (railway domain $DOMAIN)"
echo ""
echo "  ⚠  CRITICAL — do NOT remove the following Route 53 record:"
echo "       $RAILWAY_TXT_NAME  (TXT, Railway ownership token)"
echo "     Removing it revokes Railway's cert for $DOMAIN and breaks"
echo "     the SECONDARY leg of this failover."
echo ""
echo "  ℹ  Run  scripts/verify_failover.sh  periodically (or in CI/cron)"
echo "     to detect drift of the Railway edge / TXT / TLS cert."
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
