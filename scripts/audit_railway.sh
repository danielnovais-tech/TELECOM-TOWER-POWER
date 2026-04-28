#!/usr/bin/env bash
# audit_railway.sh — read-only inventory of the linked Railway project.
#
# Output: human-readable report listing services, public domains, volumes,
# and (most importantly) variables that look like service references but
# point at services that no longer exist OR are hard-coded strings where a
# ${{ ... }} reference would be more robust.
#
# Usage:
#   railway login && railway link    # one-time, picks project + environment
#   ./scripts/audit_railway.sh       # prints to stdout
#   ./scripts/audit_railway.sh > /tmp/railway-before.txt   # snapshot for diff
#
# Exit code:
#   0  — audit completed (warnings printed but not fatal)
#   2  — Railway CLI missing or not linked
#   3  — required tool missing (jq)

set -euo pipefail

require() {
    command -v "$1" >/dev/null 2>&1 || { echo "ERROR: $1 not installed" >&2; exit "${2:-3}"; }
}

require railway 2
require jq 3

# Sanity: project must be linked
if ! railway status >/dev/null 2>&1; then
    echo "ERROR: not linked to a Railway project. Run 'railway link' first." >&2
    exit 2
fi

echo "=== Railway audit — $(date -u +%FT%TZ) ==="
railway status
echo

# Pull the full project graph as JSON (single API call, then we slice it).
# `railway status --json` returns project + environment + services with vars.
project_json="$(railway status --json)"

# ---- Services -------------------------------------------------------------
echo "=== Services ==="
echo "$project_json" \
    | jq -r '
        .services[]
        | "\(.name)\t\(.latestDeployment.status // "n/a")\t\(.serviceDomains[0].domain // "-")"
    ' \
    | column -t -s $'\t' -N 'SERVICE,STATUS,PUBLIC_DOMAIN'
echo

# ---- Volumes --------------------------------------------------------------
echo "=== Volumes ==="
if railway volume list --json >/dev/null 2>&1; then
    railway volume list --json \
        | jq -r '.[] | "\(.name)\t\(.sizeMB)MB\t\(.attachedToService // "ORPHAN")"' \
        | column -t -s $'\t' -N 'VOLUME,SIZE,ATTACHED_TO'
else
    echo "(railway volume list not supported by this CLI version — check via UI)"
fi
echo

# ---- Variable reference audit --------------------------------------------
# For every service variable, classify as:
#   REF   value matches '${{ <Service>.<VAR> }}'      → resolved
#   STALE REF where <Service> doesn't exist anymore   → broken
#   LITERAL value contains '.railway.internal' but not as a reference → fragile
#   OK    everything else
echo "=== Variable reference audit ==="

# Build set of existing service names (lower-cased for case-insensitive match).
mapfile -t services < <(echo "$project_json" | jq -r '.services[].name')

is_known_service() {
    local needle="$1"
    for s in "${services[@]}"; do
        [[ "${s,,}" == "${needle,,}" ]] && return 0
    done
    return 1
}

stale=0
fragile=0

while IFS=$'\t' read -r svc var value; do
    # ${{ ServiceName.VAR }}  (Railway's reference syntax)
    if [[ "$value" =~ \$\{\{[[:space:]]*([A-Za-z0-9_-]+)\.[A-Za-z0-9_]+[[:space:]]*\}\} ]]; then
        ref_svc="${BASH_REMATCH[1]}"
        if ! is_known_service "$ref_svc"; then
            printf '  STALE REF  %-25s %-25s → ${{ %s.* }} (service does not exist)\n' \
                "$svc" "$var" "$ref_svc"
            stale=$((stale + 1))
        fi
    # Hard-coded internal hostname (won't show as edge in graph; rotates if renamed)
    elif [[ "$value" == *.railway.internal* ]]; then
        printf '  FRAGILE    %-25s %-25s = %s  (consider ${{ ... }})\n' \
            "$svc" "$var" "$value"
        fragile=$((fragile + 1))
    fi
done < <(
    echo "$project_json" \
        | jq -r '
            .services[]
            | .name as $svc
            | (.variables // {})
            | to_entries[]
            | [$svc, .key, (.value | tostring)]
            | @tsv
        '
)

if [[ "$stale" -eq 0 && "$fragile" -eq 0 ]]; then
    echo "  All references resolve to existing services. No literal *.railway.internal hostnames."
fi

echo
echo "=== Summary ==="
printf '  services:           %d\n' "${#services[@]}"
printf '  stale references:   %d\n' "$stale"
printf '  fragile literals:   %d\n' "$fragile"

# Don't fail the script — this is an inventory tool, not a gate.
exit 0
