#!/usr/bin/env bash
# Shared helpers for the cloud-smoke harness (prefect-orchestration-tyf.5).
#
# Sourced by the `provision-*`, `seed-*`, `run-smoke`, `assert-success`,
# and `teardown-*` scripts. Provides:
#   - require_cmd      : fail-fast pre-flight for required tools
#   - log / warn / die : terminal output helpers
#   - smoke_state_dir  : ./.smoke (gitignored host workspace)
#   - smoke_namespace  : k8s namespace (default po-smoke)
#   - smoke_release    : helm release name (default po)
#   - smoke_cluster    : kind cluster name (default po-smoke)
#   - smoke_registry   : local registry container name + host:port
#   - smoke_dry        : 1 when SMOKE_DRY=1 — print, don't execute
#   - run              : either echo (dry) or exec the command
#   - kubectl_cp_rig   : copy a host directory into the rig PVC via sleeper
#   - capture_start_ts / commits_since_start : exit-gate timestamp helpers
#
# Sourcing is intentional (not exec) — keeps every step in the same
# trap/cleanup scope.

set -euo pipefail

# Resolve repo root once. Scripts always source this file by absolute or
# script-dir-relative path, so $BASH_SOURCE is reliable.
SMOKE_LIB="${BASH_SOURCE[0]}"
SMOKE_SCRIPT_DIR="$(cd "$(dirname "$SMOKE_LIB")" && pwd)"
REPO_ROOT="$(cd "$SMOKE_SCRIPT_DIR/../.." && pwd)"

SMOKE_STATE_DIR="${SMOKE_STATE_DIR:-$REPO_ROOT/.smoke}"
SMOKE_NAMESPACE="${SMOKE_NAMESPACE:-po-smoke}"
SMOKE_RELEASE="${SMOKE_RELEASE:-po}"
SMOKE_CLUSTER="${SMOKE_CLUSTER:-po-smoke}"
SMOKE_REGISTRY_NAME="${SMOKE_REGISTRY_NAME:-po-smoke-registry}"
SMOKE_REGISTRY_PORT="${SMOKE_REGISTRY_PORT:-5001}"
SMOKE_REGISTRY_HOST="${SMOKE_REGISTRY_HOST:-127.0.0.1:${SMOKE_REGISTRY_PORT}}"
SMOKE_IMAGE_REPO="${SMOKE_IMAGE_REPO:-${SMOKE_REGISTRY_HOST}/po-worker}"
SMOKE_IMAGE_TAG="${SMOKE_IMAGE_TAG:-smoke}"
SMOKE_PACK_PATH="${SMOKE_PACK_PATH:-$REPO_ROOT/../software-dev/po-formulas}"
SMOKE_DRIVER="${SMOKE_DRIVER:-kind}"
SMOKE_AUTH="${SMOKE_AUTH:-apikey}"
SMOKE_ISSUE_ID="${SMOKE_ISSUE_ID:-smoke-1}"
SMOKE_TIMEOUT_MIN="${SMOKE_TIMEOUT_MIN:-20}"
SMOKE_DRY="${SMOKE_DRY:-0}"

mkdir -p "$SMOKE_STATE_DIR"

# ---------------------------------------------------------------------------
# Output helpers. Keep stderr clean so stdout can be piped/teed by callers.
# ---------------------------------------------------------------------------
log()  { printf '\033[1;34m[smoke]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[smoke:warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[smoke:err]\033[0m %s\n' "$*" >&2; exit 1; }

require_cmd() {
  local missing=()
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || missing+=("$c")
  done
  if (( ${#missing[@]} )); then
    die "missing required commands: ${missing[*]}"
  fi
}

# Print the command, then run it — unless SMOKE_DRY=1, which prints only.
# Quoting is preserved by re-quoting each arg on display.
run() {
  local rendered=""
  local arg
  for arg in "$@"; do
    if [[ "$arg" =~ [[:space:]\"\'\$\\] ]]; then
      rendered+=" $(printf '%q' "$arg")"
    else
      rendered+=" $arg"
    fi
  done
  printf '\033[2m+%s\033[0m\n' "$rendered" >&2
  if [[ "$SMOKE_DRY" == "1" ]]; then
    return 0
  fi
  "$@"
}

# Capture an ISO-8601 UTC timestamp to a state file. Used by the exit-gate
# to assert "the throwaway repo got a new commit *since the smoke started*"
# rather than "has any commit ever".
capture_start_ts() {
  date -u +%Y-%m-%dT%H:%M:%SZ > "$SMOKE_STATE_DIR/started_at"
}

# Count commits in a target git dir authored at-or-after the captured
# start timestamp. Echoes the count.
commits_since_start() {
  local repo="$1"
  local since
  since="$(cat "$SMOKE_STATE_DIR/started_at" 2>/dev/null || echo '')"
  if [[ -z "$since" ]]; then
    echo 0
    return 0
  fi
  git -C "$repo" log --since="$since" --oneline 2>/dev/null | wc -l | tr -d ' '
}

# Copy a host directory into the chart's rig PVC using a throwaway
# busybox sleeper. Caller passes ($1) the host source dir and ($2) the
# in-PVC destination relative to the rig mount.
#
# Why a sleeper: PVC mounts only attach to running pods, so `kubectl cp`
# needs *some* pod with the PVC mounted. The chart's worker pod might
# not be ready yet (or might be busy). A short-lived busybox is simpler
# and decouples seeding from worker scheduling.
kubectl_cp_rig() {
  local src="$1"
  local dest_rel="$2"
  local pod="po-smoke-seeder"
  local rig_claim="${SMOKE_RELEASE}-rig"

  log "seeding rig PVC ${rig_claim} from ${src} -> /rig/${dest_rel}"
  if [[ "$SMOKE_DRY" == "1" ]]; then
    log "(dry-run) would create sleeper, kubectl cp, then delete"
    return 0
  fi

  # Idempotent re-create: if a previous run left a stuck sleeper, kill it.
  kubectl -n "$SMOKE_NAMESPACE" delete pod "$pod" --ignore-not-found --wait=true >/dev/null

  # Inline pod spec — easier to grok than a templated YAML file.
  cat <<EOF | kubectl -n "$SMOKE_NAMESPACE" apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${pod}
  labels: {app: po-smoke-seeder}
spec:
  restartPolicy: Never
  containers:
    - name: sleeper
      image: busybox:1.36
      command: ["sh", "-c", "mkdir -p /rig && sleep 3600"]
      volumeMounts:
        - {name: rig, mountPath: /rig}
  volumes:
    - name: rig
      persistentVolumeClaim:
        claimName: ${rig_claim}
EOF

  kubectl -n "$SMOKE_NAMESPACE" wait --for=condition=Ready "pod/${pod}" --timeout=120s
  kubectl -n "$SMOKE_NAMESPACE" exec "$pod" -- mkdir -p "/rig/${dest_rel}"
  kubectl -n "$SMOKE_NAMESPACE" cp "$src/." "${pod}:/rig/${dest_rel}"
  kubectl -n "$SMOKE_NAMESPACE" delete pod "$pod" --wait=false >/dev/null
}
