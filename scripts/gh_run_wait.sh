#!/usr/bin/env bash
# Poll a GitHub Actions run until completion, then exit 0/1 based on conclusion.
#
# Usage:
#   scripts/gh_run_wait.sh <run-id> [--interval N] [--timeout N]
#   scripts/gh_run_wait.sh --workflow deploy-ecs.yml --latest
#
# Defaults: interval=10s, timeout=1800s (30min).
# Always prints one line per poll: "HH:MM:SS  status:conclusion  elapsed=Ns".
# Exits 0 on success, 1 on failure/cancelled, 124 on timeout.
set -euo pipefail

# `gh` reads $GITHUB_TOKEN over keyring; for interactive use we want keyring.
unset GITHUB_TOKEN GH_TOKEN || true

INTERVAL=10
TIMEOUT=1800
RUN_ID=""
WORKFLOW=""
LATEST=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --interval) INTERVAL="$2"; shift 2 ;;
        --timeout)  TIMEOUT="$2"; shift 2 ;;
        --workflow) WORKFLOW="$2"; shift 2 ;;
        --latest)   LATEST=1; shift ;;
        -h|--help)
            sed -n '2,12p' "$0"; exit 0 ;;
        *)  RUN_ID="$1"; shift ;;
    esac
done

if [[ -z "$RUN_ID" ]]; then
    if [[ -n "$WORKFLOW" && "$LATEST" == 1 ]]; then
        RUN_ID=$(gh run list --workflow="$WORKFLOW" -L 1 --json databaseId -q '.[0].databaseId')
    else
        echo "error: provide a run id or --workflow X --latest" >&2
        exit 2
    fi
fi

echo "Watching run $RUN_ID (interval=${INTERVAL}s, timeout=${TIMEOUT}s)"
START=$(date +%s)
while :; do
    NOW=$(date +%s)
    ELAPSED=$((NOW - START))
    STATE=$(gh run view "$RUN_ID" --json status,conclusion -q '.status+":"+.conclusion' 2>/dev/null || echo "error:")
    printf '%s  %-30s elapsed=%ds\n' "$(date +%H:%M:%S)" "$STATE" "$ELAPSED"
    case "$STATE" in
        completed:success)              exit 0 ;;
        completed:failure|completed:cancelled|completed:timed_out|completed:action_required)
            echo "Run did not succeed: $STATE" >&2
            gh run view "$RUN_ID" --log-failed 2>/dev/null | tail -40 || true
            exit 1 ;;
    esac
    if (( ELAPSED >= TIMEOUT )); then
        echo "Timeout after ${TIMEOUT}s — last state: $STATE" >&2
        exit 124
    fi
    sleep "$INTERVAL"
done
