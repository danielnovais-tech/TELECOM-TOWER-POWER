#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
#
# setup_sionna_ssm.sh — Provision SSM Parameter Store entries that the
# Sionna RT worker (and downstream API code) reads at runtime.
#
# Idempotent: each parameter is overwritten with --overwrite.
# Plaintext String type is used (no secrets here — bucket names + queue
# URLs are not sensitive). KMS-backed SecureString is reserved for the
# audit-log KMS key + Stripe webhook secret in setup_audit_kms.sh.
#
# Parameters created (under /telecom-tower-power/sionna-rt/):
#   queue-url           SQS URL the worker long-polls
#   results-bucket      S3 bucket for per-pixel loss rasters
#   scenes-bucket       S3 bucket holding the scene bundles
#   job-queue           AWS Batch job queue name
#   job-definition      AWS Batch job definition name
#   backend             "sionna_rt" or "fspl_stub"
#   max-depth           ray-tracing reflection depth (default 5)
#   samples             rays/launch (default 1_000_000)
#
# Read-side: the API (graphql_schema.py / batch_worker.py / rf_engines/
# sionna_rt_engine.py) calls boto3 ssm.get_parameter at startup. The IAM
# policy in setup_sionna_batch.sh already allows
# ssm:GetParameter on /telecom-tower-power/*.
#
# Usage:
#   ./scripts/setup_sionna_ssm.sh \
#       --account-id 490083271496 \
#       --region sa-east-1
set -euo pipefail

ACCOUNT_ID="${AWS_ACCOUNT_ID:-}"
REGION="${AWS_REGION:-sa-east-1}"
BACKEND="${SIONNA_RT_BACKEND:-sionna_rt}"
SCENES_BUCKET="${SCENES_BUCKET:-telecom-tower-power-scenes}"
RESULTS_BUCKET="${RESULTS_BUCKET:-telecom-tower-power-results}"
JQ_NAME="${JQ_NAME:-jq-sionna-rt}"
JD_NAME="${JD_NAME:-sionna-rt-worker}"
MAX_DEPTH="${MAX_DEPTH:-5}"
SAMPLES="${SAMPLES:-1000000}"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --account-id)      ACCOUNT_ID="$2"; shift 2 ;;
    --region)          REGION="$2"; shift 2 ;;
    --backend)         BACKEND="$2"; shift 2 ;;
    --scenes-bucket)   SCENES_BUCKET="$2"; shift 2 ;;
    --results-bucket)  RESULTS_BUCKET="$2"; shift 2 ;;
    --max-depth)       MAX_DEPTH="$2"; shift 2 ;;
    --samples)         SAMPLES="$2"; shift 2 ;;
    --dry-run)         DRY_RUN="true"; shift ;;
    -h|--help)         sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

: "${ACCOUNT_ID:?--account-id required}"

PREFIX="/telecom-tower-power/sionna-rt"
QUEUE_URL="https://sqs.${REGION}.amazonaws.com/${ACCOUNT_ID}/telecom-tower-power-rt-jobs"

put() {
  local name="$1" value="$2" desc="$3"
  if [[ "$DRY_RUN" == "true" ]]; then
    printf 'DRY-RUN: ssm put-parameter %s = %s\n' "$name" "$value"
    return
  fi
  aws ssm put-parameter \
    --name "$name" \
    --value "$value" \
    --type String \
    --description "$desc" \
    --overwrite \
    --region "$REGION" >/dev/null
  echo "  set $name"
}

echo "=================================================="
echo " Sionna RT — SSM parameters under $PREFIX"
echo " Region: $REGION   Account: $ACCOUNT_ID"
echo " Backend: $BACKEND"
echo "=================================================="

put "${PREFIX}/queue-url"      "$QUEUE_URL"        "SQS queue URL for sionna-rt jobs"
put "${PREFIX}/scenes-bucket"  "$SCENES_BUCKET"    "S3 bucket holding scene bundles"
put "${PREFIX}/results-bucket" "$RESULTS_BUCKET"   "S3 bucket for per-pixel loss rasters"
put "${PREFIX}/job-queue"      "$JQ_NAME"          "AWS Batch job queue (sionna-rt)"
put "${PREFIX}/job-definition" "$JD_NAME"          "AWS Batch job definition (sionna-rt)"
put "${PREFIX}/backend"        "$BACKEND"          "sionna_rt | fspl_stub"
put "${PREFIX}/max-depth"      "$MAX_DEPTH"        "ray-tracing max reflection depth"
put "${PREFIX}/samples"        "$SAMPLES"          "rays per Sionna PathSolver launch"

echo "=================================================="
echo " Done. Verify with:"
echo "   aws ssm get-parameters-by-path --path $PREFIX --region $REGION"
echo "=================================================="
