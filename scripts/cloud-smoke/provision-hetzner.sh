#!/usr/bin/env bash
# Provision a single-node Hetzner Cloud VM running k3s, write its
# kubeconfig to ./.smoke/kubeconfig, and load the worker image onto the
# node via `k3s ctr images import` (no public registry needed).
#
# Pre-reqs:
#   - hcloud CLI on PATH, authenticated (`hcloud context use …`)
#   - HCLOUD_TOKEN exported (or the active context already has one)
#   - SSH key pre-registered with Hetzner (HCLOUD_SSH_KEY=name)
#
# Cost guard: provisions one CX22 (~€0.05/hr). teardown-hetzner.sh
# reverses everything. prefect-orchestration-tyf.5.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "${HERE}/lib.sh"

require_cmd hcloud docker kubectl helm ssh scp

: "${HCLOUD_SSH_KEY:?must set HCLOUD_SSH_KEY=<hcloud ssh-key name>}"
HCLOUD_SERVER_NAME="${HCLOUD_SERVER_NAME:-po-smoke-1}"
HCLOUD_SERVER_TYPE="${HCLOUD_SERVER_TYPE:-cx22}"
HCLOUD_LOCATION="${HCLOUD_LOCATION:-fsn1}"
HCLOUD_IMAGE="${HCLOUD_IMAGE:-ubuntu-24.04}"
KUBECONFIG_OUT="${SMOKE_STATE_DIR}/kubeconfig"

log "driver=hetzner server=${HCLOUD_SERVER_NAME} (${HCLOUD_SERVER_TYPE}@${HCLOUD_LOCATION})"

# ---------------------------------------------------------------------------
# 1. Server. Reuse if one already exists with the smoke name.
# ---------------------------------------------------------------------------
if hcloud server list -o noheader 2>/dev/null | awk '{print $2}' | grep -qx "$HCLOUD_SERVER_NAME"; then
  log "server ${HCLOUD_SERVER_NAME} already exists — reusing"
else
  log "creating server ${HCLOUD_SERVER_NAME}"
  # cloud-init: install k3s + emit kubeconfig with the public IP, no traefik
  # (we only need the API + a single workload). The k3s installer is
  # idempotent on re-runs.
  run hcloud server create \
      --name "$HCLOUD_SERVER_NAME" \
      --type "$HCLOUD_SERVER_TYPE" \
      --location "$HCLOUD_LOCATION" \
      --image "$HCLOUD_IMAGE" \
      --ssh-key "$HCLOUD_SSH_KEY" \
      --user-data-from-file <(cat <<'CLOUD_INIT'
#cloud-config
package_update: true
runcmd:
  - curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--disable=traefik --write-kubeconfig-mode=644" sh -
  - until [ -s /etc/rancher/k3s/k3s.yaml ]; do sleep 2; done
  - chmod 644 /etc/rancher/k3s/k3s.yaml
CLOUD_INIT
) || warn "hcloud server create returned non-zero — check manually"
fi

if [[ "$SMOKE_DRY" == "1" ]]; then
  log "(dry-run) skipping kubeconfig fetch + image import"
  exit 0
fi

ip="$(hcloud server ip "$HCLOUD_SERVER_NAME")"
log "server IP: ${ip}"

# ---------------------------------------------------------------------------
# 2. Wait for k3s to come up + fetch kubeconfig. cloud-init takes ~60-120s
#    on a fresh CX22 so we poll with a timeout.
# ---------------------------------------------------------------------------
log "waiting for /etc/rancher/k3s/k3s.yaml on ${ip}"
deadline=$(( $(date +%s) + 300 ))
while (( $(date +%s) < deadline )); do
  if ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes "root@${ip}" \
        'test -s /etc/rancher/k3s/k3s.yaml' 2>/dev/null; then
    break
  fi
  sleep 5
done
ssh -o StrictHostKeyChecking=accept-new "root@${ip}" \
    'cat /etc/rancher/k3s/k3s.yaml' \
  | sed "s|https://127.0.0.1:6443|https://${ip}:6443|" \
  > "$KUBECONFIG_OUT"
chmod 600 "$KUBECONFIG_OUT"
export KUBECONFIG="$KUBECONFIG_OUT"
log "wrote ${KUBECONFIG_OUT} (export KUBECONFIG=${KUBECONFIG_OUT})"

kubectl get nodes -o wide

# ---------------------------------------------------------------------------
# 3. Build worker image locally, save to tar, scp + ctr import.
#    Avoids needing a public registry / imagePullSecrets.
# ---------------------------------------------------------------------------
if [[ ! -d "$SMOKE_PACK_PATH" ]]; then
  warn "pack path ${SMOKE_PACK_PATH} does not exist — building base image without pack"
  pack_args=()
else
  pack_args=(--build-context "pack=${SMOKE_PACK_PATH}")
fi

local_tag="po-worker:smoke"
tarball="${SMOKE_STATE_DIR}/po-worker-smoke.tar"
log "building ${local_tag} locally"
docker build -t "$local_tag" "${pack_args[@]}" "$REPO_ROOT"
docker save "$local_tag" -o "$tarball"
log "uploading image to ${ip}"
scp -o StrictHostKeyChecking=accept-new "$tarball" "root@${ip}:/tmp/po-worker.tar"
ssh "root@${ip}" 'k3s ctr images import /tmp/po-worker.tar && rm -f /tmp/po-worker.tar'

# Override defaults so the chart references the imported image (k3s tags
# it under the local docker name, with no registry prefix).
echo "po-worker:smoke" > "$SMOKE_STATE_DIR/image-ref"

log "ensuring namespace ${SMOKE_NAMESPACE}"
kubectl create namespace "$SMOKE_NAMESPACE" \
    --dry-run=client -o yaml | kubectl apply -f -

log "hetzner provisioning OK; KUBECONFIG=${KUBECONFIG_OUT}"
