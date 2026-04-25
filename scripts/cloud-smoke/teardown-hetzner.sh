#!/usr/bin/env bash
# Tear down the Hetzner smoke server + any associated volumes.
# Idempotent: safe to re-run on already-clean state. No blanket `|| true`.
#
# prefect-orchestration-tyf.5.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "${HERE}/lib.sh"

require_cmd hcloud

HCLOUD_SERVER_NAME="${HCLOUD_SERVER_NAME:-po-smoke-1}"

if hcloud server list -o noheader 2>/dev/null | awk '{print $2}' | grep -qx "$HCLOUD_SERVER_NAME"; then
  log "deleting hcloud server ${HCLOUD_SERVER_NAME}"
  run hcloud server delete "$HCLOUD_SERVER_NAME"
else
  log "server ${HCLOUD_SERVER_NAME} not present — skipping"
fi

# Volumes — only delete ones explicitly named for the smoke. Avoid
# blanket greps on user data.
while IFS= read -r vol; do
  [[ -z "$vol" ]] && continue
  log "deleting hcloud volume ${vol}"
  run hcloud volume delete "$vol"
done < <(hcloud volume list -o noheader 2>/dev/null \
          | awk '$2 ~ /^po-smoke-/ {print $2}')

# Load balancers — likewise.
while IFS= read -r lb; do
  [[ -z "$lb" ]] && continue
  log "deleting hcloud load-balancer ${lb}"
  run hcloud load-balancer delete "$lb"
done < <(hcloud load-balancer list -o noheader 2>/dev/null \
          | awk '$2 ~ /^po-smoke-/ {print $2}')

log "hetzner tear-down OK"
