#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
#
# smoke_sionna_batch.sh — Submit a probe-only Sionna RT job to AWS Batch
# and tail the CloudWatch log until the container reports its GPU stack.
#
# This is the production-side counterpart of
# scripts/sionna_rt_validation_gate.py: where the gate validates RMSE
# against a golden link set, this script proves a real G-instance can
# (a) pull the ECR image, (b) attach the GPU, (c) import mitsuba +
# sionna RT with a CUDA variant.
#
# T17 (2026-05-04): closes the T12 hardware-blocker by replacing the
# bench host probe (no FMA, no CUDA) with a real G5/G6 spot instance.
#
# Submits the GPU worker container with command override
# ``--probe`` (no SQS poll), waits up to ~10 minutes for the spot
# instance to spin up, prints the JSON GPU-stack snapshot, then exits.
#
# Exit codes:
#   0  job SUCCEEDED, GPU stack JSON printed
#   1  job FAILED (check the CloudWatch link printed above)
#   2  argument / pre-flight failure
#   3  job did not start within the timeout
#
# Usage:
#   ./scripts/smoke_sionna_batch.sh \
#       --region sa-east-1 \
#       --job-queue jq-sionna-rt \
#       --job-definition sionna-rt-worker
set -euo pipefail

REGION="${AWS_REGION:-sa-east-1}"
JOB_QUEUE="${JQ_NAME:-jq-sionna-rt}"
JOB_DEFINITION="${JD_NAME:-sionna-rt-worker}"
TIMEOUT_SECS="${TIMEOUT_SECS:-900}"  # 15 min

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region)         REGION="$2"; shift 2 ;;
    --job-queue)      JOB_QUEUE="$2"; shift 2 ;;
    --job-definition) JOB_DEFINITION="$2"; shift 2 ;;
    --timeout)        TIMEOUT_SECS="$2"; shift 2 ;;
    -h|--help)        sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

JOB_NAME="sionna-rt-smoke-$(date +%s)"
echo "submitting probe job: $JOB_NAME"
JOB_ID=$(aws batch submit-job \
  --region "$REGION" \
  --job-name "$JOB_NAME" \
  --job-queue "$JOB_QUEUE" \
  --job-definition "$JOB_DEFINITION" \
  --container-overrides '{"command": ["--probe"]}' \
  --query 'jobId' --output text)
echo "jobId=$JOB_ID"
echo "watch: https://console.aws.amazon.com/batch/home?region=${REGION}#jobs/detail/${JOB_ID}"

START_TS=$(date +%s)
LAST_STATUS=""
while true; do
  NOW=$(date +%s)
  if (( NOW - START_TS > TIMEOUT_SECS )); then
    echo "ERROR: job did not reach a terminal state within ${TIMEOUT_SECS}s" >&2
    exit 3
  fi
  STATUS=$(aws batch describe-jobs --region "$REGION" --jobs "$JOB_ID" \
            --query 'jobs[0].status' --output text 2>/dev/null || echo "UNKNOWN")
  if [[ "$STATUS" != "$LAST_STATUS" ]]; then
    echo "[$(date +%H:%M:%S)] $STATUS"
    LAST_STATUS="$STATUS"
  fi
  case "$STATUS" in
    SUCCEEDED|FAILED) break ;;
  esac
  sleep 10
done

LOG_STREAM=$(aws batch describe-jobs --region "$REGION" --jobs "$JOB_ID" \
              --query 'jobs[0].container.logStreamName' --output text)
if [[ -n "$LOG_STREAM" && "$LOG_STREAM" != "None" ]]; then
  echo
  echo "── /aws/batch/sionna-rt-worker / $LOG_STREAM ──"
  aws logs get-log-events --region "$REGION" \
        --log-group-name "/aws/batch/sionna-rt-worker" \
        --log-stream-name "$LOG_STREAM" \
        --no-start-from-head \
        --query 'events[].message' --output text \
    | tr '\t' '\n' | tail -50
fi

if [[ "$STATUS" == "SUCCEEDED" ]]; then
  echo
  echo "✓ smoke run SUCCEEDED — Sionna RT GPU stack is healthy on $JOB_QUEUE"
  exit 0
fi
echo
echo "✗ smoke run FAILED (status=$STATUS)" >&2
exit 1
