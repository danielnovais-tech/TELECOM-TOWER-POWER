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
#   4. The edge may stop presenting a cert that matches
#      api.telecomtowerpower.com.br (verification lapsed, renewal failed,
#      custom domain detached, etc.).
#
# This script performs ONLY read operations and exits non-zero on drift,
# so it is safe to run from cron / CI. It does NOT modify DNS.
#
# Exit codes:
#   0  all checks passed
#   1  drift detected (see stderr or JSON output)
#   2  usage / environment error
#
# Usage:
#   ./scripts/verify_failover.sh
#   ./scripts/verify_failover.sh --json          # machine-readable output
#   ./scripts/verify_failover.sh --json --quiet  # JSON only, no human text
#   RAILWAY_DNS=xyz.up.railway.app ./scripts/verify_failover.sh  # override
# ============================================================================

ROOT_DOMAIN="telecomtowerpower.com.br"
DOMAIN="api.telecomtowerpower.com.br"
RAILWAY_DNS="${RAILWAY_DNS:-i1fuknjg.up.railway.app}"
RAILWAY_TXT_NAME="_railway-verify.api.${ROOT_DOMAIN}"

JSON=0
QUIET=0
for arg in "$@"; do
  case "$arg" in
    --json)  JSON=1 ;;
    --quiet) QUIET=1 ;;
    -h|--help)
      sed -n '1,40p' "$0"; exit 0 ;;
    *)
      echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

# Structured results per check. Values are "pass" | "fail" | "skip".
declare -A RESULT DETAIL
OBSERVED_SECONDARY=""
EXPECTED_SECONDARY="$RAILWAY_DNS"
OBSERVED_PUBLIC_CNAME=""

fail=0
say()  { (( QUIET == 1 )) || printf '%s\n' "$*"; }
note() { (( QUIET == 1 )) || printf '  ✓ %s\n' "$*"; }
warn() { (( QUIET == 1 )) || printf '  ✗ %s\n' "$*" >&2; fail=1; }
skip() { (( QUIET == 1 )) || printf '  ⚠ %s\n' "$*"; }
# bump fail counter even in quiet mode
mark_fail() { fail=1; }

command -v aws >/dev/null 2>&1 || { echo "aws CLI required" >&2; exit 2; }

say "=== Failover drift check for $DOMAIN ==="

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
say "▸ Route 53 SECONDARY CNAME for $DOMAIN"
secondary=$(aws route53 list-resource-record-sets \
  --hosted-zone-id "$R53_ZONE_ID" \
  --query "ResourceRecordSets[?Name==\`${DOMAIN}.\` && Type==\`CNAME\` && Failover==\`SECONDARY\`].ResourceRecords[0].Value | [0]" \
  --output text --no-cli-pager 2>/dev/null || true)
OBSERVED_SECONDARY="${secondary//None/}"
if [[ -z "$OBSERVED_SECONDARY" ]]; then
  RESULT[route53_secondary_matches]=fail
  DETAIL[route53_secondary_matches]="SECONDARY CNAME missing"
  mark_fail
  warn "SECONDARY CNAME for $DOMAIN is missing"
elif [[ "$OBSERVED_SECONDARY" != "$EXPECTED_SECONDARY" ]]; then
  RESULT[route53_secondary_matches]=fail
  DETAIL[route53_secondary_matches]="observed=$OBSERVED_SECONDARY expected=$EXPECTED_SECONDARY"
  mark_fail
  warn "SECONDARY CNAME = $OBSERVED_SECONDARY  (expected $EXPECTED_SECONDARY)"
else
  RESULT[route53_secondary_matches]=pass
  DETAIL[route53_secondary_matches]="$OBSERVED_SECONDARY"
  note "SECONDARY CNAME = $OBSERVED_SECONDARY"
fi

# ── 3. _railway-verify TXT ownership token ─────────────────────────
say "▸ Railway ownership TXT record"
txt_count=$(aws route53 list-resource-record-sets \
  --hosted-zone-id "$R53_ZONE_ID" \
  --query "ResourceRecordSets[?Name==\`${RAILWAY_TXT_NAME}.\` && Type==\`TXT\`] | length(@)" \
  --output text --no-cli-pager 2>/dev/null || echo 0)
if [[ "$txt_count" =~ ^[0-9]+$ ]] && (( txt_count > 0 )); then
  RESULT[txt_present]=pass
  DETAIL[txt_present]="${txt_count} record set(s)"
  note "$RAILWAY_TXT_NAME present"
else
  RESULT[txt_present]=fail
  DETAIL[txt_present]="missing"
  mark_fail
  warn "$RAILWAY_TXT_NAME is MISSING — Railway will (or will soon) stop serving a cert for $DOMAIN"
fi

# ── 4. Railway edge resolves ──────────────────────────────────────
say "▸ Railway edge DNS"
if getent hosts "$RAILWAY_DNS" >/dev/null 2>&1 || host "$RAILWAY_DNS" >/dev/null 2>&1; then
  RESULT[edge_resolves]=pass
  DETAIL[edge_resolves]="$RAILWAY_DNS"
  note "$RAILWAY_DNS resolves"
else
  RESULT[edge_resolves]=fail
  DETAIL[edge_resolves]="$RAILWAY_DNS does not resolve"
  mark_fail
  warn "$RAILWAY_DNS does not resolve"
fi

# ── 5. Railway edge serves a cert valid for $DOMAIN ───────────────
say "▸ Railway TLS cert"
if command -v openssl >/dev/null 2>&1; then
  cert=$(echo | timeout 10 openssl s_client \
      -connect "${RAILWAY_DNS}:443" \
      -servername "$DOMAIN" 2>/dev/null \
      | openssl x509 -noout -text 2>/dev/null || true)
  if [[ -z "$cert" ]]; then
    RESULT[cert_ok]=fail
    DETAIL[cert_ok]="could not retrieve cert from $RAILWAY_DNS"
    mark_fail
    warn "could not retrieve cert from $RAILWAY_DNS (network / edge down?)"
  elif echo "$cert" | grep -Eq "DNS:${DOMAIN}([^A-Za-z0-9.-]|$)|CN *= *${DOMAIN}"; then
    RESULT[cert_ok]=pass
    DETAIL[cert_ok]="covers $DOMAIN"
    note "edge presents a cert covering $DOMAIN"
  else
    RESULT[cert_ok]=fail
    DETAIL[cert_ok]="cert does not cover $DOMAIN"
    mark_fail
    warn "edge cert does NOT cover $DOMAIN (TLS would fail on failover)"
  fi
else
  RESULT[cert_ok]=skip
  DETAIL[cert_ok]="openssl not available"
  skip "openssl not available — skipping TLS check"
fi

# ── 6. Live public resolution sanity check ────────────────────────
say "▸ Public resolution of $DOMAIN"
if command -v dig >/dev/null 2>&1; then
  OBSERVED_PUBLIC_CNAME=$(dig +short "$DOMAIN" CNAME @1.1.1.1 2>/dev/null | head -1 | sed 's/\.$//')
  if [[ -n "$OBSERVED_PUBLIC_CNAME" ]]; then
    note "current public CNAME → $OBSERVED_PUBLIC_CNAME"
  else
    say "  ℹ no CNAME in public answer (may be A-alias / primary healthy)"
  fi
fi

# ── JSON output (hand-rolled, no jq dependency) ───────────────────
if (( JSON == 1 )); then
  esc() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
  overall_ok=true
  for k in route53_secondary_matches txt_present edge_resolves cert_ok; do
    [[ "${RESULT[$k]:-skip}" == "fail" ]] && overall_ok=false
  done
  cat <<EOF
{
  "domain": "$(esc "$DOMAIN")",
  "expected_secondary": "$(esc "$EXPECTED_SECONDARY")",
  "observed_secondary": "$(esc "$OBSERVED_SECONDARY")",
  "observed_public_cname": "$(esc "$OBSERVED_PUBLIC_CNAME")",
  "checks": {
    "route53_secondary_matches": { "status": "${RESULT[route53_secondary_matches]:-skip}", "detail": "$(esc "${DETAIL[route53_secondary_matches]:-}")" },
    "txt_present":               { "status": "${RESULT[txt_present]:-skip}",               "detail": "$(esc "${DETAIL[txt_present]:-}")" },
    "edge_resolves":             { "status": "${RESULT[edge_resolves]:-skip}",             "detail": "$(esc "${DETAIL[edge_resolves]:-}")" },
    "cert_ok":                   { "status": "${RESULT[cert_ok]:-skip}",                   "detail": "$(esc "${DETAIL[cert_ok]:-}")" }
  },
  "ok": ${overall_ok}
}
EOF
fi

say ""
if (( fail == 0 )); then
  (( QUIET == 1 )) || echo "✓ No failover drift detected."
  exit 0
else
  (( QUIET == 1 )) || echo "✗ Drift detected — see messages above. DNS was NOT modified." >&2
  exit 1
fi
