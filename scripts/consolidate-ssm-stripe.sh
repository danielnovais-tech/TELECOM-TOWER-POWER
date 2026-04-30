#!/usr/bin/env bash
# consolidate-ssm-stripe.sh
# -------------------------
# Audit duplicate SSM parameter paths for Stripe and (optionally) collapse them
# to the canonical `/telecom-tower-power/STRIPE_*` path used by ECS task def.
#
# Why: the pre-launch audit found Stripe creds at multiple paths (e.g.
# `/STRIPE_SECRET_KEY` and `/prod/STRIPE_SECRET_KEY`) plus the canonical
# `/telecom-tower-power/STRIPE_SECRET_KEY`. The running container's behaviour
# becomes opaque; an env-var rename can silently flip the active set.
#
# This script:
#   1. Lists every SSM parameter whose name contains STRIPE_*.
#   2. For each known logical name, prints all paths it lives under, masking
#      the value, and flags which paths disagree.
#   3. With --apply, deletes the non-canonical paths AFTER verifying that the
#      canonical path holds the most recently modified value (or matches it).
#
# It NEVER overwrites the canonical path; if a non-canonical copy is newer the
# script aborts so the operator can decide which one wins.
#
# Usage:
#   ./scripts/consolidate-ssm-stripe.sh                # dry-run audit
#   ./scripts/consolidate-ssm-stripe.sh --apply        # delete non-canonical
#
# Requires: aws CLI v2, jq, AWS_REGION (or AWS_DEFAULT_REGION) set.

set -euo pipefail

CANONICAL_PREFIX="/telecom-tower-power"
LOGICAL_NAMES=(
  STRIPE_SECRET_KEY
  STRIPE_WEBHOOK_SECRET
  STRIPE_PRICE_STARTER
  STRIPE_PRICE_PRO
  STRIPE_PRICE_BUSINESS
  STRIPE_PRICE_ENTERPRISE
)

APPLY=0
case "${1:-}" in
  --apply) APPLY=1 ;;
  ""|--dry-run) APPLY=0 ;;
  *) echo "usage: $0 [--apply|--dry-run]" >&2; exit 2 ;;
esac

REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-sa-east-1}}"
echo "Region: $REGION   Mode: $([[ $APPLY == 1 ]] && echo APPLY || echo DRY-RUN)"
echo

mask() { local v="${1:-}"; [[ -z "$v" ]] && { echo "(empty)"; return; }; echo "${v:0:6}…${v: -4} (len=${#v})"; }

# Collect all parameters that mention any logical Stripe name.
NAMES_JSON=$(aws ssm describe-parameters --region "$REGION" \
  --parameter-filters "Key=Name,Option=Contains,Values=STRIPE" \
  --query 'Parameters[].{Name:Name,LastModifiedDate:LastModifiedDate}' \
  --output json)

if [[ "$(echo "$NAMES_JSON" | jq 'length')" == 0 ]]; then
  echo "No SSM parameters containing 'STRIPE' found."
  exit 0
fi

EXIT=0
for logical in "${LOGICAL_NAMES[@]}"; do
  echo "=== $logical ==="
  matches=$(echo "$NAMES_JSON" | jq -r --arg n "$logical" '.[] | select(.Name | endswith("/" + $n) or . == $n or . == ("/"+$n)) | .Name')
  if [[ -z "$matches" ]]; then
    echo "  (not found)"
    continue
  fi

  canonical="$CANONICAL_PREFIX/$logical"
  has_canonical=0
  declare -A vals=()
  declare -A mtimes=()

  while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    val=$(aws ssm get-parameter --region "$REGION" --name "$path" --with-decryption \
            --query 'Parameter.Value' --output text 2>/dev/null || echo "")
    mtime=$(aws ssm describe-parameters --region "$REGION" \
              --parameter-filters "Key=Name,Values=$path" \
              --query 'Parameters[0].LastModifiedDate' --output text 2>/dev/null || echo "")
    vals["$path"]="$val"
    mtimes["$path"]="$mtime"
    [[ "$path" == "$canonical" ]] && has_canonical=1
    printf '  %-60s mtime=%s value=%s\n' "$path" "$mtime" "$(mask "$val")"
  done <<< "$matches"

  if [[ $has_canonical -eq 0 ]]; then
    echo "  ! No canonical path ${canonical} exists. Promote one of the above first; aborting."
    EXIT=1
    continue
  fi

  canonical_val="${vals[$canonical]}"
  canonical_mtime="${mtimes[$canonical]}"
  conflicts=()
  for path in "${!vals[@]}"; do
    [[ "$path" == "$canonical" ]] && continue
    if [[ "${vals[$path]}" != "$canonical_val" ]]; then
      # value differs; keep canonical only if canonical mtime is >= the other.
      if [[ "${mtimes[$path]}" > "$canonical_mtime" ]]; then
        echo "  !! ${path} has a NEWER value than ${canonical} — refusing to delete."
        echo "     Manually decide which value wins (sync canonical first)."
        conflicts+=("$path")
        EXIT=1
      else
        conflicts+=("$path")
        echo "  - ${path} differs from canonical (canonical is newer; safe to drop)."
      fi
    else
      conflicts+=("$path")
    fi
  done

  if [[ ${#conflicts[@]} -eq 0 ]]; then
    echo "  ok — only canonical path exists."
    continue
  fi

  for p in "${conflicts[@]}"; do
    if [[ "${mtimes[$p]:-}" > "$canonical_mtime" ]]; then continue; fi  # already aborted
    if [[ $APPLY -eq 1 ]]; then
      aws ssm delete-parameter --region "$REGION" --name "$p"
      echo "  deleted: $p"
    else
      echo "  would delete: $p"
    fi
  done
  unset vals mtimes
done

echo
if [[ $APPLY -eq 0 ]]; then
  echo "Dry-run complete. Re-run with --apply to delete duplicates."
else
  echo "Apply complete (exit=$EXIT)."
fi
exit "$EXIT"
