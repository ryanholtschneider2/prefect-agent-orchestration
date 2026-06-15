#!/usr/bin/env bash
# Migrate a dolt-backed beads rig's data into beads_rust (br).
#
# br rejects dots in issue ids, and PO's legacy iter-bead convention was
# `<seed>.<step>.iter<N>`. This script exports the dolt rig to JSONL, rewrites
# every dotted iter-bead id (and dependency ref) to the new hyphen form via
# `migrate_jsonl_ids.py`, stages the rewritten export over the rig's configured
# `.beads/<jsonl_export>`, then imports it into br with `br sync --import-only`.
#
# Usage:
#   setup/migrate-dolt-to-br.sh <rig_path>
#
# Env:
#   BD_BIN   path to the dolt `bd` binary (default: bd)
#   BR_BIN   path to the `br` binary       (default: br)
#
# Idempotent on the rewrite step; the br import is upsert-by-id (re-running is
# safe — up-to-date issues are skipped). Inspect the rewritten
# `<rig>/.beads/export.hyphenated.jsonl` before import if you want to eyeball
# the id changes first.
#
# Note: `br sync --import-only` (br >= 0.1.x) reads from the rig's *configured*
# JSONL path (`.beads/<jsonl_export>`, default `issues.jsonl`) — it does NOT
# take a positional file. So we stage the rewritten export over that path
# (backing up any existing one) and then run the bare import.
set -euo pipefail

RIG_PATH="${1:?usage: migrate-dolt-to-br.sh <rig_path>}"
BD_BIN="${BD_BIN:-bd}"
BR_BIN="${BR_BIN:-br}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BEADS_DIR="${RIG_PATH%/}/.beads"
RAW_EXPORT="${BEADS_DIR}/export.jsonl"
HYPHEN_EXPORT="${BEADS_DIR}/export.hyphenated.jsonl"

if [[ ! -d "${BEADS_DIR}" ]]; then
  echo "error: ${BEADS_DIR} not found — is ${RIG_PATH} a beads rig?" >&2
  exit 1
fi

echo "1/4 exporting dolt rig -> ${RAW_EXPORT}"
"${BD_BIN}" export --json --all >"${RAW_EXPORT}" 2>/dev/null \
  || (cd "${RIG_PATH}" && "${BD_BIN}" export --json --all >"${RAW_EXPORT}")

echo "2/4 rewriting dotted iter ids -> ${HYPHEN_EXPORT}"
python3 "${SCRIPT_DIR}/migrate_jsonl_ids.py" "${RAW_EXPORT}" >"${HYPHEN_EXPORT}"

# Resolve the configured JSONL export path from metadata.json (default
# issues.jsonl) — that is the file `br sync --import-only` reads.
JSONL_NAME="$(python3 -c "import json,sys; print(json.load(open('${BEADS_DIR}/metadata.json')).get('jsonl_export','issues.jsonl'))" 2>/dev/null || echo issues.jsonl)"
JSONL_PATH="${BEADS_DIR}/${JSONL_NAME}"

echo "3/4 staging rewritten export over ${JSONL_PATH}"
if [[ -f "${JSONL_PATH}" ]]; then
  cp -f "${JSONL_PATH}" "${JSONL_PATH}.pre-migrate.bak"
fi
cp -f "${HYPHEN_EXPORT}" "${JSONL_PATH}"

echo "4/4 importing into br (br sync --import-only)"
(cd "${RIG_PATH}" && "${BR_BIN}" sync --import-only)

echo "done — rewritten export at ${HYPHEN_EXPORT}, staged at ${JSONL_PATH}"
