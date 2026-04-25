#!/usr/bin/env bash
# Create the k8s Secret(s) the chart references for Claude auth. Runs
# AFTER `helm install` so the chart's Secret reference resolves but
# BEFORE the worker pod is rescheduled with credentials available.
#
# Modes:
#   apikey  — reads $ANTHROPIC_API_KEY from host env, creates Secret
#             `anthropic-api-key` with key `ANTHROPIC_API_KEY`.
#   oauth   — reads ~/.claude/.credentials.json, creates Secret
#             `claude-oauth` with key `credentials.json`.
#
# Refuses to run if the requested credential source is absent — no
# silent fallback (CLAUDE.md "Silent fallbacks in distributed
# pipelines"). Never echoes credential bytes; uses `--from-literal=` /
# `--from-file=` so secrets stay out of process listings + shell
# history. prefect-orchestration-tyf.5.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "${HERE}/lib.sh"

require_cmd kubectl

mode="${SMOKE_AUTH:-apikey}"
log "credential mode: ${mode}"

# Disable trace mode locally — extra paranoia. Caller might be in
# `set -x` higher up.
{ set +x; } 2>/dev/null

case "$mode" in
  apikey)
    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
      die "SMOKE_AUTH=apikey but ANTHROPIC_API_KEY is unset"
    fi
    secret_name="${SMOKE_APIKEY_SECRET:-anthropic-api-key}"
    log "creating Secret ${secret_name} (mode=apikey)"
    if [[ "$SMOKE_DRY" == "1" ]]; then
      log "(dry-run) would kubectl create secret generic ${secret_name}"
      exit 0
    fi
    # Idempotent replace so re-running the smoke after a key rotation works.
    kubectl -n "$SMOKE_NAMESPACE" create secret generic "$secret_name" \
        --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
        --dry-run=client -o yaml \
      | kubectl -n "$SMOKE_NAMESPACE" apply -f -
    ;;
  oauth)
    creds="${HOME}/.claude/.credentials.json"
    if [[ ! -s "$creds" ]]; then
      die "SMOKE_AUTH=oauth but ${creds} is missing or empty"
    fi
    secret_name="${SMOKE_OAUTH_SECRET:-claude-oauth}"
    log "creating Secret ${secret_name} (mode=oauth)"
    if [[ "$SMOKE_DRY" == "1" ]]; then
      log "(dry-run) would kubectl create secret generic ${secret_name} --from-file=…"
      exit 0
    fi
    kubectl -n "$SMOKE_NAMESPACE" create secret generic "$secret_name" \
        --from-file=credentials.json="$creds" \
        --dry-run=client -o yaml \
      | kubectl -n "$SMOKE_NAMESPACE" apply -f -
    ;;
  *)
    die "unknown SMOKE_AUTH=${mode} (expected apikey|oauth)"
    ;;
esac

log "credential secret in place"
