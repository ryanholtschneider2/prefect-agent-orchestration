#!/usr/bin/env bash
# Exit-gate for the cloud smoke. Polls until both conditions hold or a
# timeout (SMOKE_TIMEOUT_MIN, default 20m) is reached:
#
#   1. The smoke bead reports status=closed (via `bd show --json` run
#      inside the worker pod where the rig PVC is mounted).
#   2. The rig's smoke-target git working tree has at least one new
#      commit since `lib.sh::capture_start_ts` was called.
#
# Echoes a single-line summary and exits 0 on success / non-zero on
# failure (which the orchestrator's trap turns into a tear-down).
#
# prefect-orchestration-tyf.5.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "${HERE}/lib.sh"

require_cmd kubectl

issue_id="${SMOKE_ISSUE_ID}"
if [[ -z "${issue_id}" || "$issue_id" == "smoke-1" ]]; then
  # Default fallthrough: prefer the id seed-rig.sh wrote.
  if [[ -s "${SMOKE_STATE_DIR}/issue_id" ]]; then
    issue_id="$(cat "${SMOKE_STATE_DIR}/issue_id")"
  fi
fi
[[ -n "$issue_id" ]] || die "no issue id to assert against"

deadline=$(( $(date +%s) + SMOKE_TIMEOUT_MIN * 60 ))
worker_deploy="${SMOKE_RELEASE}-worker"

log "polling bd status for ${issue_id} (timeout ${SMOKE_TIMEOUT_MIN}m)"

bd_in_pod() {
  # Run `bd` inside any worker replica with the rig PVC mounted.
  kubectl -n "$SMOKE_NAMESPACE" exec "deploy/${worker_deploy}" -- \
      sh -c "cd /rig/smoke-target && bd show ${issue_id} --json" 2>/dev/null || true
}

git_in_pod() {
  kubectl -n "$SMOKE_NAMESPACE" exec "deploy/${worker_deploy}" -- \
      sh -c "cd /rig/smoke-target && git log --since=\"$(cat "${SMOKE_STATE_DIR}/started_at" 2>/dev/null || echo '@0')\" --oneline" \
      2>/dev/null || true
}

bd_status=""
new_commits=0
while (( $(date +%s) < deadline )); do
  bd_json="$(bd_in_pod)"
  if [[ -n "$bd_json" ]]; then
    bd_status="$(printf '%s\n' "$bd_json" \
        | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
    if isinstance(d, list): d = d[0]
    print(d.get("status",""))
except Exception:
    print("")')"
  fi
  commits="$(git_in_pod | wc -l | tr -d ' ')"
  new_commits="${commits:-0}"
  log "bd_status=${bd_status:-?} new_commits=${new_commits}"
  if [[ "$bd_status" == "closed" && "$new_commits" -ge 1 ]]; then
    sha="$(git_in_pod | head -n1 | awk '{print $1}')"
    printf '\033[1;32m=== smoke OK: bead %s closed, commit %s on target ===\033[0m\n' \
        "$issue_id" "$sha" >&2
    exit 0
  fi
  sleep 30
done

die "smoke timed out: bd_status='${bd_status}', new_commits=${new_commits}"
