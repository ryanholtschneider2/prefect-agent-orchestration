#!/usr/bin/env bash
# Local docker-compose smoke for prefect-orchestration-j2p.
#
# Brings the stack up, ensures the `po` work-pool exists, and runs one issue
# through `software-dev-full` against the containerized worker. The default
# uses `PO_BACKEND=stub` so the smoke does not require an Anthropic API
# key — flip to `cli` after exporting `ANTHROPIC_API_KEY` to run real
# Claude.
#
# Pre-reqs: docker compose v2, a `./rig/` directory with a `.beads/` init
# (run `bd init` inside it once) and at least one open issue.

set -euo pipefail

cd "$(dirname "$0")/.."

ISSUE_ID="${ISSUE_ID:-demo-1}"
RIG_DIR="${RIG_DIR:-./rig}"
PO_BACKEND="${PO_BACKEND:-stub}"
export PO_BACKEND

# When real Claude is selected, the worker will refuse to start without
# an API key. Surface that early instead of letting `docker compose up`
# fail opaquely.
if [[ "$PO_BACKEND" == "cli" && -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "error: PO_BACKEND=cli but ANTHROPIC_API_KEY is unset." >&2
  echo "       export ANTHROPIC_API_KEY=… or set PO_BACKEND=stub." >&2
  exit 64
fi
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-stub-not-required}"

if [[ ! -d "$RIG_DIR/.beads" ]]; then
  echo "error: $RIG_DIR has no .beads/ — run \`bd init\` inside it first." >&2
  exit 2
fi

echo ">>> building worker image"
docker compose build worker

echo ">>> bringing up prefect-server + worker"
docker compose up -d prefect-server worker

echo ">>> ensuring 'po' work-pool exists on the server"
docker compose run --rm --entrypoint /bin/sh client -c \
    "prefect work-pool create po --type process 2>/dev/null || true"

echo ">>> running po doctor"
docker compose run --rm client doctor

echo ">>> running software-dev-full on $ISSUE_ID (PO_BACKEND=$PO_BACKEND)"
docker compose run --rm \
    -e PO_BACKEND \
    client run software-dev-full \
        --issue-id "$ISSUE_ID" \
        --rig demo \
        --rig-path /rig

echo ">>> smoke complete"
