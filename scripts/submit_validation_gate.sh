#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
#
# submit_validation_gate.sh
#
# Registers (if missing) the `sionna-rt-validation-gate` Batch job
# definition and submits a one-off job that runs
# `scripts/sionna_rt_validation_gate.py` against the bundled golden
# link set, uploads the JSON result to S3, and exits 0/1/2 based on
# the gate criteria (see scripts/sionna_rt_validation_gate.py docstring).
#
# Usage:
#   ./scripts/submit_validation_gate.sh \
#     --account-id 490083271496 \
#     --region sa-east-1 \
#     --image-tag latest \
#     --result-bucket telecom-tower-power-results
#
# Optional:
#   --queue jq-sionna-rt
#   --sub6-rmse-db-max 6.0
#   --mmwave-delta-db-min 10.0
#   --jd-template sionna-rt-validation-job-definition.json
#   --register-only   (register/refresh JD without submitting)
#   --dry-run
set -euo pipefail

ACCOUNT_ID="${AWS_ACCOUNT_ID:-}"
REGION="${AWS_REGION:-sa-east-1}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
RESULT_BUCKET="${RESULT_BUCKET:-telecom-tower-power-results}"
QUEUE="${BATCH_QUEUE:-jq-sionna-rt}"
JD_NAME="sionna-rt-validation-gate"
JD_TEMPLATE=""
SUB6_RMSE_DB_MAX="6.0"
MMWAVE_DELTA_DB_MIN="10.0"
REGISTER_ONLY="false"
DRY_RUN="false"

usage() {
  sed -n '5,30p' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --account-id) ACCOUNT_ID="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --image-tag) IMAGE_TAG="$2"; shift 2 ;;
    --result-bucket) RESULT_BUCKET="$2"; shift 2 ;;
    --queue) QUEUE="$2"; shift 2 ;;
    --sub6-rmse-db-max) SUB6_RMSE_DB_MAX="$2"; shift 2 ;;
    --mmwave-delta-db-min) MMWAVE_DELTA_DB_MIN="$2"; shift 2 ;;
    --jd-template) JD_TEMPLATE="$2"; shift 2 ;;
    --register-only) REGISTER_ONLY="true"; shift ;;
    --dry-run) DRY_RUN="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$ACCOUNT_ID" ]]; then
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
fi
: "${ACCOUNT_ID:?--account-id required}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -z "$JD_TEMPLATE" ]]; then
  JD_TEMPLATE="$REPO_ROOT/sionna-rt-validation-job-definition.json"
fi
[[ -f "$JD_TEMPLATE" ]] || { echo "ERROR: template not found: $JD_TEMPLATE" >&2; exit 2; }

run() {
  if [[ "$DRY_RUN" == "true" ]]; then
    printf 'DRY-RUN: %s\n' "$*"
  else
    "$@"
  fi
}

echo "=================================================="
echo " Sionna RT Validation Gate — Batch submission"
echo " Account:        $ACCOUNT_ID"
echo " Region:         $REGION"
echo " Image tag:      $IMAGE_TAG"
echo " Job queue:      $QUEUE"
echo " Job def:        $JD_NAME"
echo " Result bucket:  $RESULT_BUCKET"
echo " sub6 RMSE max:  $SUB6_RMSE_DB_MAX dB"
echo " mmwave Δ min:   $MMWAVE_DELTA_DB_MIN dB"
echo " Dry-run:        $DRY_RUN"
echo "=================================================="

# Render the JD template with current account/region/tag.
RENDERED=$(mktemp /tmp/rt-validation-jd-XXXXXX.json)
sed -e "s|ACCOUNT_ID|${ACCOUNT_ID}|g" \
    -e "s|AWS_REGION|${REGION}|g" \
    -e "s|IMAGE_TAG|${IMAGE_TAG}|g" \
    "$JD_TEMPLATE" > "$RENDERED"

echo "[1/2] Register/refresh job definition $JD_NAME"
run aws batch register-job-definition --region "$REGION" \
  --cli-input-json "file://${RENDERED}" \
  --query 'jobDefinitionArn' --output text
rm -f "$RENDERED"

if [[ "$REGISTER_ONLY" == "true" ]]; then
  echo "Done (register-only)."
  exit 0
fi

# Submit the job with overrides for thresholds and result S3 URI.
TS="$(date -u +%Y%m%d-%H%M%S)"
JOB_NAME="rt-validation-${TS}"
RESULT_KEY="validation-gate/${TS}.json"
RESULT_S3_URI="s3://${RESULT_BUCKET}/${RESULT_KEY}"

echo "[2/2] Submit job $JOB_NAME"
OVERRIDES=$(cat <<JSON
{
  "environment": [
    {"name": "RESULT_S3_URI", "value": "${RESULT_S3_URI}"},
    {"name": "SUB6_RMSE_DB_MAX", "value": "${SUB6_RMSE_DB_MAX}"},
    {"name": "MMWAVE_DELTA_DB_MIN", "value": "${MMWAVE_DELTA_DB_MIN}"}
  ]
}
JSON
)

if [[ "$DRY_RUN" == "true" ]]; then
  printf 'DRY-RUN: aws batch submit-job --job-name %s --job-queue %s --job-definition %s\n' \
    "$JOB_NAME" "$QUEUE" "$JD_NAME"
  printf 'DRY-RUN: containerOverrides=%s\n' "$OVERRIDES"
  echo "Would upload result to: $RESULT_S3_URI"
  exit 0
fi

JOB_ID=$(aws batch submit-job --region "$REGION" \
  --job-name "$JOB_NAME" \
  --job-queue "$QUEUE" \
  --job-definition "$JD_NAME" \
  --container-overrides "$OVERRIDES" \
  --query 'jobId' --output text)

echo
echo "Submitted: $JOB_ID"
echo "Result will be uploaded to: $RESULT_S3_URI"
echo
echo "Watch status:"
echo "  aws batch describe-jobs --region $REGION --jobs $JOB_ID --query 'jobs[0].[status,statusReason]' --output text"
echo
echo "Tail logs (after RUNNING):"
echo "  aws logs tail /aws/batch/sionna-rt-worker --region $REGION --since 5m --follow --log-stream-name-prefix validation"
echo
echo "Fetch result JSON:"
echo "  aws s3 cp $RESULT_S3_URI -"
