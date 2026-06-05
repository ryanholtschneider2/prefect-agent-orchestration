#!/usr/bin/env bash
# Migrate a dolt-backed beads rig's data into beads_rust (br).
#
# br rejects dots in issue ids, and PO's legacy iter-bead convention was
# `<seed>.<step>.iter<N>`. This script exports the dolt rig to JSONL, rewrites
# every dotted iter-bead id (and dependency ref) to the new hyphen form via
# `migrate_jsonl_ids.py`, then imports the rewritten export into br with
# `br sync --import-only`.
#
# Usage:
#   setup/migrate-dolt-to-br.sh <rig_path>
#
# Env:
#   BD_BIN   path to the dolt `bd` binary (default: bd)
#   BR_BIN   path to the `br` binary       (default: br)
#
# Idempotent on the rewrite step; the br import is not (re-running appends).
# Inspect the rewritten `<rig>/.beads/export.hyphenated.jsonl` before import
# if you want to eyeball the id changes first.
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

echo "1/3 exporting dolt rig -> ${RAW_EXPORT}"
"${BD_BIN}" export --json --all >"${RAW_EXPORT}" 2>/dev/null \
  || (cd "${RIG_PATH}" && "${BD_BIN}" export --json --all >"${RAW_EXPORT}")

echo "2/3 rewriting dotted iter ids -> ${HYPHEN_EXPORT}"
python3 "${SCRIPT_DIR}/migrate_jsonl_ids.py" "${RAW_EXPORT}" >"${HYPHEN_EXPORT}"

echo "3/3 importing into br (br sync --import-only)"
(cd "${RIG_PATH}" && "${BR_BIN}" sync --import-only "${HYPHEN_EXPORT}")

echo "done — rewritten export at ${HYPHEN_EXPORT}"
