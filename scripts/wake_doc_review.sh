#!/usr/bin/env bash
set -euo pipefail

TARGET_AT=""
RUN_ID="doc-review-$(date '+%Y%m%d-%H%M%S')"
LOG_DIR="logs/wake-doc-review"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-at)
      TARGET_AT="$2"
      shift 2
      ;;
    --run-id)
      RUN_ID="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$TARGET_AT" ]]; then
  echo "--release-at is required, e.g. '2026-06-08 01:30:00'" >&2
  exit 2
fi

mkdir -p "$LOG_DIR"
LOG_PATH="$LOG_DIR/$RUN_ID.log"

target_epoch="$(date -j -f '%Y-%m-%d %H:%M:%S' "$TARGET_AT" '+%s')"

{
  echo "run_id=$RUN_ID"
  echo "started_at=$(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "release_at=$TARGET_AT"
  echo "post_release_plan=review workflow-engine config/AC docs and all related workflow/Anki handoff docs"
} | tee -a "$LOG_PATH"

while [[ "$(date '+%s')" -lt "$target_epoch" ]]; do
  now="$(date '+%Y-%m-%d %H:%M:%S %Z')"
  remaining=$((target_epoch - $(date '+%s')))
  echo "waiting now=$now remaining_s=$remaining" | tee -a "$LOG_PATH"
  sleep 300
done

echo "released_at=$(date '+%Y-%m-%d %H:%M:%S %Z')" | tee -a "$LOG_PATH"
echo "release_reason=time_reached" | tee -a "$LOG_PATH"
