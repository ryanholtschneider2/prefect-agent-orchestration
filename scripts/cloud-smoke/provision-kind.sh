#!/usr/bin/env bash
# Provision a single-node kind cluster + a local image registry + a
# fresh worker image, ready for `helm install po ./charts/po`.
#
# Idempotent: re-running on an existing cluster/registry/image is a
# no-op (each step is existence-checked).
#
# prefect-orchestration-tyf.5.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "${HERE}/lib.sh"

require_cmd docker kind kubectl helm

log "driver=kind cluster=${SMOKE_CLUSTER} registry=${SMOKE_REGISTRY_HOST}"

# ---------------------------------------------------------------------------
# 1. Local image registry. We run a `registry:2` container on the host
#    docker network exposing 127.0.0.1:${SMOKE_REGISTRY_PORT}, then connect
#    it to the kind network so cluster nodes can pull from it. This is the
#    pattern documented in https://kind.sigs.k8s.io/docs/user/local-registry/.
# ---------------------------------------------------------------------------
if ! docker inspect "$SMOKE_REGISTRY_NAME" >/dev/null 2>&1; then
  log "starting local registry container ${SMOKE_REGISTRY_NAME}"
  run docker run -d --restart=always \
      -p "127.0.0.1:${SMOKE_REGISTRY_PORT}:5000" \
      --name "$SMOKE_REGISTRY_NAME" \
      registry:2
else
  log "registry ${SMOKE_REGISTRY_NAME} already running — reusing"
fi

# ---------------------------------------------------------------------------
# 2. Kind cluster with containerd configured to treat the local registry
#    as a known mirror (so `image: 127.0.0.1:5001/...` references resolve).
# ---------------------------------------------------------------------------
if kind get clusters 2>/dev/null | grep -qx "$SMOKE_CLUSTER"; then
  log "kind cluster ${SMOKE_CLUSTER} already exists — reusing"
else
  log "creating kind cluster ${SMOKE_CLUSTER}"
  if [[ "$SMOKE_DRY" == "1" ]]; then
    log "(dry-run) would create kind cluster"
  else
    cat <<EOF | kind create cluster --name "$SMOKE_CLUSTER" --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
containerdConfigPatches:
  - |-
    [plugins."io.containerd.grpc.v1.cri".registry.mirrors."${SMOKE_REGISTRY_HOST}"]
      endpoint = ["http://${SMOKE_REGISTRY_NAME}:5000"]
EOF
  fi
fi

# Connect the registry container to the kind network so the
# `${SMOKE_REGISTRY_NAME}:5000` mirror endpoint resolves inside the node.
if [[ "$SMOKE_DRY" != "1" ]]; then
  if ! docker network inspect kind \
        --format '{{range .Containers}}{{.Name}}{{"\n"}}{{end}}' \
        2>/dev/null | grep -qx "$SMOKE_REGISTRY_NAME"; then
    log "connecting ${SMOKE_REGISTRY_NAME} to kind network"
    docker network connect kind "$SMOKE_REGISTRY_NAME" || true
  fi
fi

# ---------------------------------------------------------------------------
# 3. Build + push the worker image. Bakes the software-dev pack as a
#    build-context so `po list` is non-empty inside the pod.
# ---------------------------------------------------------------------------
if [[ ! -d "$SMOKE_PACK_PATH" ]]; then
  warn "pack path ${SMOKE_PACK_PATH} does not exist — building base image without pack"
  warn "set SMOKE_PACK_PATH=… to bake a pack into the image"
  pack_args=()
else
  pack_args=(--build-context "pack=${SMOKE_PACK_PATH}")
fi

log "building ${SMOKE_IMAGE_REPO}:${SMOKE_IMAGE_TAG}"
run docker build -t "${SMOKE_IMAGE_REPO}:${SMOKE_IMAGE_TAG}" \
    "${pack_args[@]}" \
    "$REPO_ROOT"
log "pushing image to local registry"
run docker push "${SMOKE_IMAGE_REPO}:${SMOKE_IMAGE_TAG}"

# ---------------------------------------------------------------------------
# 4. Namespace.
# ---------------------------------------------------------------------------
log "ensuring namespace ${SMOKE_NAMESPACE}"
run kubectl create namespace "$SMOKE_NAMESPACE" \
    --dry-run=client -o yaml | kubectl apply -f -

log "kind provisioning OK"
