#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.

# Fetch QGIS/Atoll artifacts from a GitHub Actions run.
#
# Why this script exists:
# - Some gh CLI versions do not support "gh run view --json artifacts".
# - QGIS export runs always publish artifacts via upload-artifact; this script
#   uses the REST API directly so artifact discovery is stable.
#
# Usage:
#   scripts/fetch_qgis_artifact.sh --run-id 25293853926
#   scripts/fetch_qgis_artifact.sh --run-id 25293853926 --list-only
#   scripts/fetch_qgis_artifact.sh --run-id 25293853926 --no-extract
#   scripts/fetch_qgis_artifact.sh --run-id 25293853926 --out-dir artifacts
#
# Defaults:
# - repository: inferred from git remotes (owner/repo)
# - out-dir: ./artifacts
# - extract: enabled (extracts *.tar.gz into <name>_extracted)

set -euo pipefail

RUN_ID=""
REPO=""
OUT_DIR="artifacts"
LIST_ONLY=0
EXTRACT=1

usage() {
    sed -n '2,30p' "$0"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-id)
            RUN_ID="$2"
            shift 2
            ;;
        --repo)
            REPO="$2"
            shift 2
            ;;
        --out-dir)
            OUT_DIR="$2"
            shift 2
            ;;
        --list-only)
            LIST_ONLY=1
            shift
            ;;
        --no-extract)
            EXTRACT=0
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "error: unknown argument: $1" >&2
            usage
            exit 2
            ;;
    esac
done

if [[ -z "$RUN_ID" ]]; then
    echo "error: --run-id is required" >&2
    usage
    exit 2
fi

if [[ -z "$REPO" ]]; then
    REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
fi

echo "run-id: $RUN_ID"
echo "repo:   $REPO"

COUNT=$(gh api "repos/$REPO/actions/runs/$RUN_ID/artifacts" --jq '.total_count')

if [[ "$COUNT" == "0" ]]; then
    echo "no artifacts found for run $RUN_ID"
    exit 1
fi

echo "artifacts:"
gh api "repos/$REPO/actions/runs/$RUN_ID/artifacts" --jq '.artifacts[] | "- \(.name) (\(.size_in_bytes) bytes)"'

if [[ "$LIST_ONLY" == "1" ]]; then
    exit 0
fi

mkdir -p "$OUT_DIR"
echo "downloading all artifacts to: $OUT_DIR"
gh run download "$RUN_ID" --dir "$OUT_DIR"

if [[ "$EXTRACT" == "1" ]]; then
    while IFS= read -r tgz; do
        base="$(basename "$tgz" .tar.gz)"
        dest="$OUT_DIR/${base}_extracted"
        mkdir -p "$dest"
        tar -xzf "$tgz" -C "$dest"
        echo "extracted: $tgz -> $dest"
    done < <(find "$OUT_DIR" -maxdepth 2 -type f -name '*.tar.gz' | sort)
fi

echo "done"
