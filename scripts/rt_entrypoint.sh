#!/bin/bash
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
#
# Dispatch entrypoint for telecom-tower-power-rt GPU image.
#
# Modes:
#   validate-gate   Run scripts/sionna_rt_validation_gate.py against the
#                   bundled golden link set, optionally uploading the JSON
#                   result to $RESULT_S3_URI.
#   probe           Report GPU stack versions and exit.
#   poll | <other>  Forwarded to scripts/sionna_rt_worker.py (default).
#
# Env vars consumed by validate-gate:
#   LINKS_PATH               (default /opt/rt/data/sionna_rt_golden_links.json)
#   OUT                      (default /tmp/rt_gate.json)
#   SUB6_RMSE_DB_MAX         (default 6.0)
#   MMWAVE_DELTA_DB_MIN      (default 10.0)
#   RESULT_S3_URI            (optional; if set, upload result JSON via aws s3 cp)
set -uo pipefail

case "${1:-}" in
  validate-gate)
    shift
    LINKS_PATH="${LINKS_PATH:-/opt/rt/data/sionna_rt_golden_links.json}"
    OUT="${OUT:-/tmp/rt_gate.json}"
    SUB6_RMSE_DB_MAX="${SUB6_RMSE_DB_MAX:-6.0}"
    MMWAVE_DELTA_DB_MIN="${MMWAVE_DELTA_DB_MIN:-10.0}"
    set +e
    python /opt/rt/scripts/sionna_rt_validation_gate.py \
      --links "$LINKS_PATH" \
      --output "$OUT" \
      --sub6-rmse-db-max "$SUB6_RMSE_DB_MAX" \
      --mmwave-delta-db-min "$MMWAVE_DELTA_DB_MIN" \
      "$@"
    STATUS=$?
    set -e
    if [[ -n "${RESULT_S3_URI:-}" && -f "$OUT" ]]; then
      aws s3 cp "$OUT" "$RESULT_S3_URI" || echo "warning: result upload failed" >&2
    fi
    if [[ -f "$OUT" ]]; then
      cat "$OUT"
    fi
    exit "$STATUS"
    ;;
  probe)
    exec python /opt/rt/scripts/sionna_rt_worker.py --probe
    ;;
  *)
    exec python /opt/rt/scripts/sionna_rt_worker.py "$@"
    ;;
esac
