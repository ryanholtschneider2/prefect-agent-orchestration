#!/usr/bin/env bash
# Tear down the kind smoke cluster + the local registry container.
# Idempotent: safe to re-run on already-clean state. No blanket `|| true`.
#
# prefect-orchestration-tyf.5.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "${HERE}/lib.sh"

require_cmd kind docker

if kind get clusters 2>/dev/null | grep -qx "$SMOKE_CLUSTER"; then
  log "deleting kind cluster ${SMOKE_CLUSTER}"
  run kind delete cluster --name "$SMOKE_CLUSTER"
else
  log "kind cluster ${SMOKE_CLUSTER} not present — skipping"
fi

if docker inspect "$SMOKE_REGISTRY_NAME" >/dev/null 2>&1; then
  log "removing registry container ${SMOKE_REGISTRY_NAME}"
  run docker rm -f "$SMOKE_REGISTRY_NAME"
else
  log "registry ${SMOKE_REGISTRY_NAME} not present — skipping"
fi

log "kind tear-down OK"
