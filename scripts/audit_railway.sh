#!/usr/bin/env bash
# audit_railway.sh — read-only inventory of the linked Railway project.
#
# Output: human-readable report listing services, volumes, and (most
# importantly) variables that look like service references but point at
# services that no longer exist OR are hard-coded *.railway.internal
# hostnames where a ${{ ... }} reference would be more robust.
#
# Usage:
#   railway login && railway link    # one-time, picks project + environment
#   ./scripts/audit_railway.sh       # prints to stdout
#   ./scripts/audit_railway.sh > /tmp/railway-after.txt   # snapshot for diff
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

if ! railway status >/dev/null 2>&1; then
    echo "ERROR: not linked to a Railway project. Run 'railway link' first." >&2
    exit 2
fi

echo "=== Railway audit — $(date -u +%FT%TZ) ==="
railway status | grep -E '^(Project|Environment):'
echo

project_json="$(railway status --json)"

# Service list lives at .services.edges[].node.{id,name}
mapfile -t services < <(echo "$project_json" | jq -r '.services.edges[].node.name' | sort)

echo "=== Services (${#services[@]}) ==="
printf '  %s\n' "${services[@]}"
echo

# ---- Volumes --------------------------------------------------------------
echo "=== Volumes ==="
if railway volume list 2>/dev/null | sed -n '1,200p'; then
    :
else
    echo "(railway volume list unavailable — check via UI)"
fi
echo

# ---- Variable reference audit --------------------------------------------
echo "=== Variable reference audit ==="

is_known_service() {
    local needle="$1"
    for s in "${services[@]}"; do
        [[ "${s,,}" == "${needle,,}" ]] && return 0
    done
    return 1
}

stale=0
fragile=0
checked=0

# Variables that Railway injects automatically per-service. They contain the
# service's *own* private hostname and cannot be replaced with a ${{...}}
# reference, so flagging them just produces noise.
is_auto_injected() {
    case "$1" in
        RAILWAY_PRIVATE_DOMAIN|RAILWAY_PUBLIC_DOMAIN|RAILWAY_TCP_PROXY_*|\
        RAILWAY_STATIC_URL|RAILWAY_GIT_*|RAILWAY_*_ID|RAILWAY_*_NAME|\
        RAILWAY_ENVIRONMENT*|RAILWAY_PROJECT_*|RAILWAY_SERVICE_*) return 0 ;;
    esac
    return 1
}

# Plugin/template self-references: e.g. Postgres exposing PGHOST pointing at
# its own postgres.railway.internal. Only flag literals on a *consumer*.
is_self_reference() {
    local svc_lower="${1,,}"
    local host_lower="$2"
    [[ "$host_lower" == "${svc_lower}.railway.internal" ]]
}

for svc in "${services[@]}"; do
    vars_json="$(railway variables --service "$svc" --json 2>/dev/null || echo '{}')"
    [[ "$vars_json" == "{}" || -z "$vars_json" ]] && continue

    while IFS=$'\t' read -r var value; do
        checked=$((checked + 1))
        is_auto_injected "$var" && continue

        if [[ "$value" =~ \$\{\{[[:space:]]*([A-Za-z0-9_-]+)\.[A-Za-z0-9_]+[[:space:]]*\}\} ]]; then
            ref_svc="${BASH_REMATCH[1]}"
            if ! is_known_service "$ref_svc"; then
                printf '  STALE REF  %-30s %-30s → ${{ %s.* }} (service not found)\n' \
                    "$svc" "$var" "$ref_svc"
                stale=$((stale + 1))
            fi
        elif [[ "$value" =~ ([a-zA-Z0-9-]+)\.railway\.internal ]]; then
            host="${BASH_REMATCH[1]}.railway.internal"
            is_self_reference "$svc" "$host" && continue
            printf '  FRAGILE    %-30s %-30s = %s\n' "$svc" "$var" "$value"
            fragile=$((fragile + 1))
        fi
    done < <(echo "$vars_json" | jq -r 'to_entries[] | [.key, (.value | tostring)] | @tsv')
done

if [[ "$stale" -eq 0 && "$fragile" -eq 0 ]]; then
    echo "  All references resolve to existing services. No literal *.railway.internal hostnames."
fi

echo
echo "=== Summary ==="
printf '  services:           %d\n' "${#services[@]}"
printf '  variables checked:  %d\n' "$checked"
printf '  stale references:   %d\n' "$stale"
printf '  fragile literals:   %d\n' "$fragile"

exit 0
