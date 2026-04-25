#!/usr/bin/env bash
# Top-level orchestrator for the cloud smoke. Wires:
#
#   provision-${driver}.sh         # cluster + image + namespace
#   helm install / helm test       # chart from charts/po
#   seed-credentials.sh            # apikey | oauth Secret
#   seed-rig.sh                    # throwaway target repo + bd init + PVC seed
#   po run software-dev-full       # via a one-shot pod on the worker image
#   assert-success.sh              # exit-gate (bead closed + commit landed)
#   teardown-${driver}.sh          # via EXIT trap, even on failure
#
# Env knobs (all optional, see engdocs/cloud-smoke.md):
#
#   SMOKE_DRIVER  kind | hetzner   (default kind)
#   SMOKE_AUTH    apikey | oauth   (default apikey)
#   SMOKE_DRY     1                (skip cluster ops, just print)
#   SMOKE_KEEP    1                (skip tear-down on success — debugging)
#
# prefect-orchestration-tyf.5.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "${HERE}/lib.sh"

# CLI flag passthroughs for convenience. Env wins for everything else.
for arg in "$@"; do
  case "$arg" in
    --dry-run) export SMOKE_DRY=1 ;;
    --keep)    export SMOKE_KEEP=1 ;;
    --hetzner) export SMOKE_DRIVER=hetzner ;;
    --kind)    export SMOKE_DRIVER=kind ;;
    --oauth)   export SMOKE_AUTH=oauth ;;
    --apikey)  export SMOKE_AUTH=apikey ;;
    -h|--help)
      grep -E '^# ' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) die "unknown arg: ${arg}" ;;
  esac
done

case "$SMOKE_DRIVER" in
  kind|hetzner) ;;
  *) die "SMOKE_DRIVER must be kind|hetzner (got ${SMOKE_DRIVER})" ;;
esac
case "$SMOKE_AUTH" in
  apikey|oauth) ;;
  *) die "SMOKE_AUTH must be apikey|oauth (got ${SMOKE_AUTH})" ;;
esac

# Tear-down trap. Runs on success (unless SMOKE_KEEP=1), on failure,
# and on Ctrl-C — so a half-provisioned cluster never lingers.
cleanup() {
  local rc=$?
  if [[ "${SMOKE_KEEP:-0}" == "1" && "$rc" == "0" ]]; then
    log "SMOKE_KEEP=1 — skipping tear-down"
    return
  fi
  log "tear-down (driver=${SMOKE_DRIVER}, exit=${rc})"
  if ! "${HERE}/teardown-${SMOKE_DRIVER}.sh"; then
    warn "tear-down reported errors — check cluster manually"
  fi
}
trap cleanup EXIT INT TERM

capture_start_ts

log "==> 1/6 provision (${SMOKE_DRIVER})"
"${HERE}/provision-${SMOKE_DRIVER}.sh"

# Pick image ref — kind path uses the registry mirror; hetzner path
# imports directly into containerd and writes the bare image:tag.
image_ref="${SMOKE_IMAGE_REPO}:${SMOKE_IMAGE_TAG}"
if [[ "$SMOKE_DRIVER" == "hetzner" && -s "${SMOKE_STATE_DIR}/image-ref" ]]; then
  image_ref="$(cat "${SMOKE_STATE_DIR}/image-ref")"
fi
image_repo="${image_ref%:*}"
image_tag="${image_ref##*:}"

log "==> 2/6 helm install"
helm_args=(
  --set "worker.image.repository=${image_repo}"
  --set "worker.image.tag=${image_tag}"
  --set "auth.mode=${SMOKE_AUTH}"
)
run helm upgrade --install "$SMOKE_RELEASE" "${REPO_ROOT}/charts/po" \
    -n "$SMOKE_NAMESPACE" --create-namespace "${helm_args[@]}"

log "==> 3/6 seed credentials"
"${HERE}/seed-credentials.sh"

if [[ "$SMOKE_DRY" != "1" ]]; then
  log "waiting for worker rollout"
  run kubectl -n "$SMOKE_NAMESPACE" rollout status \
      "deployment/${SMOKE_RELEASE}-worker" --timeout=180s
fi

log "==> 4/6 seed rig"
"${HERE}/seed-rig.sh"
SMOKE_ISSUE_ID="$(cat "${SMOKE_STATE_DIR}/issue_id" 2>/dev/null || echo "$SMOKE_ISSUE_ID")"
export SMOKE_ISSUE_ID

log "==> 5/6 trigger software-dev-full on ${SMOKE_ISSUE_ID}"
trigger_pod="po-smoke-trigger-$(date +%s)"
if [[ "$SMOKE_DRY" == "1" ]]; then
  log "(dry-run) would: kubectl run ${trigger_pod} ... po run software-dev-full"
else
  # We exec the trigger as a one-shot pod sharing the rig PVC. The pod
  # uses the same image as the worker (`po` CLI on PATH).
  cat <<EOF | kubectl -n "$SMOKE_NAMESPACE" apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${trigger_pod}
  labels: {app: po-smoke-trigger}
spec:
  restartPolicy: Never
  containers:
    - name: trigger
      image: ${image_ref}
      imagePullPolicy: IfNotPresent
      command: ["po"]
      args:
        - run
        - software-dev-full
        - --issue-id
        - "${SMOKE_ISSUE_ID}"
        - --rig
        - smoke
        - --rig-path
        - /rig/smoke-target
      env:
        - name: PREFECT_API_URL
          value: http://${SMOKE_RELEASE}-prefect-server:4200/api
        - name: PO_BACKEND
          value: cli
      volumeMounts:
        - {name: rig, mountPath: /rig}
  volumes:
    - name: rig
      persistentVolumeClaim:
        claimName: ${SMOKE_RELEASE}-rig
EOF
  log "trigger pod ${trigger_pod} created — streaming logs in background"
  ( kubectl -n "$SMOKE_NAMESPACE" logs -f "pod/${trigger_pod}" 2>&1 \
      | sed 's/^/[trigger] /' ) &
  trigger_logs_pid=$!
fi

log "==> 6/6 assert success"
if "${HERE}/assert-success.sh"; then
  rc=0
else
  rc=$?
fi

if [[ "$SMOKE_DRY" != "1" && -n "${trigger_logs_pid:-}" ]]; then
  kill "$trigger_logs_pid" 2>/dev/null || true
  wait "$trigger_logs_pid" 2>/dev/null || true
fi

exit "$rc"
