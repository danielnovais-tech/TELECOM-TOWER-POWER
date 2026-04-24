#!/usr/bin/env bash
set -uo pipefail

# ============================================================================
# verify_failover.sh — Read-only drift detector for the ALB↔Railway failover
#
# Addresses the operational risks introduced by pinning a Railway edge
# hostname and a TXT ownership token in Route 53:
#
#   1. Railway may rotate the per-custom-domain edge host.
#   2. The _railway-verify.api TXT record can be accidentally removed,
#      which silently revokes Railway's Let's Encrypt cert for the
#      custom domain — failover would surface as a TLS error.
#   3. Route 53 SECONDARY CNAME can drift away from the current Railway edge.
#   4. The edge may stop presenting a cert that matches api.telecomtowerpower.com.br
#      (verification lapsed, renewal failed, custom domain detached, etc.).
#
# This script performs ONLY read operations and exits non-zero on drift,
# so it is safe to run from cron / CI. It does NOT modify DNS.
#
# Exit codes:
#   0  all checks passed
#   1  drift detected (see stderr)
#   2  usage / environment error
#
# Usage:
#   ./scripts/verify_failover.sh
#   RAILWAY_DNS=xyz.up.railway.app ./scripts/verify_failover.sh   # override
# ============================================================================

ROOT_DOMAIN="telecomtowerpower.com.br"
DOMAIN="api.telecomtowerpower.com.br"
RAILWAY_DNS="${RAILWAY_DNS:-i1fuknjg.up.railway.app}"
RAILWAY_TXT_NAME="_railway-verify.api.${ROOT_DOMAIN}"

fail=0
note()  { printf '  ✓ %s\n' "$*"; }
warn()  { printf '  ✗ %s\n' "$*" >&2; fail=1; }

command -v aws >/dev/null 2>&1 || { echo "aws CLI required" >&2; exit 2; }

echo "=== Failover drift check for $DOMAIN ==="

# ── 1. Hosted zone lookup ─────────────────────────────────────────
R53_ZONE_ID=$(aws route53 list-hosted-zones-by-name \
  --dns-name "$ROOT_DOMAIN" \
  --query "HostedZones[?Name==\`${ROOT_DOMAIN}.\`].Id" \
  --output text --no-cli-pager 2>/dev/null | sed 's|/hostedzone/||')
if [[ -z "$R53_ZONE_ID" || "$R53_ZONE_ID" == "None" ]]; then
  echo "Hosted zone for $ROOT_DOMAIN not found" >&2
  exit 2
fi

# ── 2. Route 53 SECONDARY CNAME value ─────────────────────────────
echo "▸ Route 53 SECONDARY CNAME for $DOMAIN"
secondary=$(aws route53 list-resource-record-sets \
  --hosted-zone-id "$R53_ZONE_ID" \
  --query "ResourceRecordSets[?Name==\`${DOMAIN}.\` && Type==\`CNAME\` && Failover==\`SECONDARY\`].ResourceRecords[0].Value | [0]" \
  --output text --no-cli-pager 2>/dev/null || true)
if [[ -z "$secondary" || "$secondary" == "None" ]]; then
  warn "SECONDARY CNAME for $DOMAIN is missing"
elif [[ "$secondary" != "$RAILWAY_DNS" ]]; then
  warn "SECONDARY CNAME = $secondary  (expected $RAILWAY_DNS)"
else
  note "SECONDARY CNAME = $secondary"
fi

# ── 3. _railway-verify TXT ownership token ─────────────────────────
echo "▸ Railway ownership TXT record"
txt_count=$(aws route53 list-resource-record-sets \
  --hosted-zone-id "$R53_ZONE_ID" \
  --query "ResourceRecordSets[?Name==\`${RAILWAY_TXT_NAME}.\` && Type==\`TXT\`] | length(@)" \
  --output text --no-cli-pager 2>/dev/null || echo 0)
if [[ "$txt_count" =~ ^[0-9]+$ ]] && (( txt_count > 0 )); then
  note "$RAILWAY_TXT_NAME present"
else
  warn "$RAILWAY_TXT_NAME is MISSING — Railway will (or will soon) stop serving a cert for $DOMAIN"
fi

# ── 4. Railway edge resolves ──────────────────────────────────────
echo "▸ Railway edge DNS"
if getent hosts "$RAILWAY_DNS" >/dev/null 2>&1 || host "$RAILWAY_DNS" >/dev/null 2>&1; then
  note "$RAILWAY_DNS resolves"
else
  warn "$RAILWAY_DNS does not resolve"
fi

# ── 5. Railway edge serves a cert valid for $DOMAIN ───────────────
echo "▸ Railway TLS cert"
if command -v openssl >/dev/null 2>&1; then
  cert=$(echo | timeout 10 openssl s_client \
      -connect "${RAILWAY_DNS}:443" \
      -servername "$DOMAIN" 2>/dev/null \
      | openssl x509 -noout -text 2>/dev/null || true)
  if [[ -z "$cert" ]]; then
    warn "could not retrieve cert from $RAILWAY_DNS (network / edge down?)"
  elif echo "$cert" | grep -Eq "DNS:${DOMAIN}([^A-Za-z0-9.-]|$)|CN *= *${DOMAIN}"; then
    note "edge presents a cert covering $DOMAIN"
  else
    warn "edge cert does NOT cover $DOMAIN (TLS would fail on failover)"
  fi
else
  echo "  ⚠ openssl not available — skipping TLS check"
fi

# ── 6. Live public resolution sanity check ────────────────────────
# Detect if a public resolver still points at a long-dead target.
echo "▸ Public resolution of $DOMAIN"
if command -v dig >/dev/null 2>&1; then
  live=$(dig +short "$DOMAIN" CNAME @1.1.1.1 2>/dev/null | head -1 | sed 's/\.$//')
  if [[ -n "$live" ]]; then
    note "current public CNAME → $live"
  else
    # Failover may return only an A/alias answer depending on health state;
    # don't fail just for this.
    echo "  ℹ no CNAME in public answer (may be A-alias / primary healthy)"
  fi
fi

echo ""
if (( fail == 0 )); then
  echo "✓ No failover drift detected."
  exit 0
else
  echo "✗ Drift detected — see messages above. DNS was NOT modified." >&2
  exit 1
fi
