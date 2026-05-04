#!/usr/bin/env bash
# Phase 1 drive-test pre-flight + ingest helpers.
#
# Subcommands:
#   preflight                              health + baseline + tier sanity
#   verify-calibration <rx_calibration.json>
#                                          jq null-check (refuses nulls)
#   dryrun  <csv> <tower_meta.json> <rx_calibration.json> [source]
#                                          uploader --dry-run
#   ingest  <csv> <tower_meta.json> <rx_calibration.json> [source]
#                                          full POST to prod
#   retrain <min_links>                    triggers retrain-sionna.yml (auto)
#   metrics                                snapshot residual histogram
#
# Env:
#   TTP_DRIVETEST_KEY (Pro tier)  default: demo_ttp_pro_2604
#   TTP_API           default: https://api.telecomtowerpower.com.br

set -euo pipefail

API="${TTP_API:-https://api.telecomtowerpower.com.br}"
KEY="${TTP_DRIVETEST_KEY:-demo_ttp_pro_2604}"

_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPLOADER="$_repo_root/scripts/drivetest_to_observations.py"

cmd_preflight() {
  echo "=== /health ==="
  curl -fsS --max-time 10 "$API/health" | jq '{status, towers_in_db, db_backend, jobs_queued}'
  echo
  echo "=== link_observations baseline ==="
  curl -fsS --max-time 10 -H "X-API-Key: $KEY" \
    "$API/coverage/observations/stats" | jq .
  echo
  echo "=== rate-limit smoke (5 rapid GETs — Pro should NOT 429) ==="
  for i in 1 2 3 4 5; do
    code=$(curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $KEY" \
      "$API/coverage/observations/stats")
    echo "  hit $i -> $code"
    [[ "$code" == "429" ]] && { echo "FAIL: key is rate-limited (not Pro tier)"; exit 1; }
  done
  echo "OK preflight"
}

cmd_verify_calibration() {
  local jf="${1:?usage: verify-calibration <json>}"
  jq -e '
    .rx_gain_dbi != null and
    .rx_height_m != null and
    ([.cable_loss_db[] | select(. == null)] | length) == 0
  ' "$jf" >/dev/null \
    || { echo "FAIL: $jf still has null calibration values — measure them"; exit 1; }
  echo "OK $jf fully calibrated"
  jq '{rx_gain_dbi, rx_height_m, cable_loss_db}' "$jf"
}

cmd_dryrun() {
  local csv="${1:?}"; local meta="${2:?}"; local cal="${3:?}"; local src="${4:-drivetest_pilot}"
  cmd_verify_calibration "$cal"
  python "$UPLOADER" --csv "$csv" --tower-meta "$meta" \
    --rx-calibration "$cal" --source "$src" --dry-run \
    --out /tmp/dt_payload.json
  echo "OK payload at /tmp/dt_payload.json"
  jq '[.observations[] | keys[]] | unique' /tmp/dt_payload.json
}

cmd_ingest() {
  local csv="${1:?}"; local meta="${2:?}"; local cal="${3:?}"; local src="${4:-drivetest_pilot}"
  cmd_verify_calibration "$cal"
  local before
  before=$(curl -fsS -H "X-API-Key: $KEY" "$API/coverage/observations/stats" \
           | jq -r .link_observations)
  echo "before: $before rows"
  python "$UPLOADER" --csv "$csv" --tower-meta "$meta" \
    --rx-calibration "$cal" --source "$src" \
    --api "$API" --api-key "$KEY" --batch-size 500
  local after
  after=$(curl -fsS -H "X-API-Key: $KEY" "$API/coverage/observations/stats" \
          | jq -r .link_observations)
  echo "after: $after rows (delta=$((after - before)))"
}

cmd_retrain() {
  local min_links="${1:?usage: retrain <min_links>}"
  local current
  current=$(curl -fsS -H "X-API-Key: $KEY" "$API/coverage/observations/stats" \
            | jq -r .link_observations)
  if (( current < min_links )); then
    echo "REFUSE: link_observations=$current < min_links=$min_links"
    exit 1
  fi
  gh workflow run retrain-sionna.yml \
    -f min_links="$min_links" \
    -f exclude_synthetic=auto \
    -f force=false \
    -f dry_run=false
  echo "OK retrain dispatched (min_links=$min_links, current=$current)"
  gh run list --workflow=retrain-sionna.yml --limit 3
}

cmd_metrics() {
  curl -fsS --max-time 15 "$API/metrics" \
    | grep -E "^coverage_observation_residual_db" \
    | head -40
}

case "${1:-}" in
  preflight)            shift; cmd_preflight "$@" ;;
  verify-calibration)   shift; cmd_verify_calibration "$@" ;;
  dryrun)               shift; cmd_dryrun "$@" ;;
  ingest)               shift; cmd_ingest "$@" ;;
  retrain)              shift; cmd_retrain "$@" ;;
  metrics)              shift; cmd_metrics "$@" ;;
  *) sed -n '1,30p' "$0"; exit 2 ;;
esac
