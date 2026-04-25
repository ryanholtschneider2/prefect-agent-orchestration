#!/usr/bin/env bash
# Build a throwaway target git repo on the host (`./.smoke/target-<utc>.git`,
# bare, with a regular working clone), `bd init` inside the working clone,
# create one trivial open bead, then push the working tree onto the rig
# PVC at `/rig/smoke-target`. The flow's builder will commit + `git push`
# back into the bare repo; the exit-gate inspects the bare repo's log.
#
# prefect-orchestration-tyf.5.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "${HERE}/lib.sh"

require_cmd git kubectl bd

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
bare="${SMOKE_STATE_DIR}/target-${stamp}.git"
work="${SMOKE_STATE_DIR}/target-${stamp}"

log "creating throwaway bare repo ${bare}"
run git init --bare -b main "$bare"

log "creating working clone ${work}"
run git clone "$bare" "$work"
(
  cd "$work"
  git config user.email "smoke@po.local"
  git config user.name  "po-smoke"
  : > README.md  # placeholder for the bead's "add a comment" task
  echo "# smoke-target" > README.md
  git add README.md
  git commit -m "smoke: seed README"
  git push origin main
)

log "initializing beads in working clone"
(
  cd "$work"
  bd init >/dev/null
  # Capture the issue id we're going to drive end-to-end.
  bd create \
      --title "smoke: add a TODO comment to README" \
      --description "End-to-end smoke target — append \`<!-- smoke ok -->\` to README.md and commit." \
      --type task --priority 2 \
      --acceptance "README.md contains the smoke marker; bead closes" \
    | tee "${SMOKE_STATE_DIR}/bd-create.txt"
)

# bd create prints the new id on stdout; pluck it out for the trigger step.
issue_id="$(grep -oE 'bd-[a-z0-9]+|[a-z0-9]+-[0-9]+' "${SMOKE_STATE_DIR}/bd-create.txt" | head -n1 || true)"
if [[ -z "$issue_id" ]]; then
  # bd printed something we don't recognize — fall back to bd ready.
  issue_id="$(cd "$work" && bd ready --limit 1 --json 2>/dev/null \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])' 2>/dev/null || true)"
fi
if [[ -z "$issue_id" ]]; then
  die "could not determine the smoke bead id"
fi
echo "$issue_id" > "${SMOKE_STATE_DIR}/issue_id"
log "smoke bead id: ${issue_id}"

# Hand bare repo path back to the worker via a side-channel file. The
# trigger step bakes this into the rig too so `git push` resolves.
echo "$bare" > "${SMOKE_STATE_DIR}/bare_path"

# Inside the rig, point `origin` at a path the *pod* can reach. Two
# options:
#   1. local bare repo accessible only from host  (kind: shared docker
#      socket lets us mount-bind it; k3s on a remote host: would need a
#      git-daemon container — out of scope for this iter).
#   2. push directly from host into the bare repo when the worker is
#      done — but that loses the "real network round-trip" we wanted.
# For the kind-driver default, we copy the working clone (with `.git`)
# onto the PVC and rewrite `origin` to a `file://` path that resolves
# from inside the pod via a hostPath bind not yet wired in tyf.4.
#
# Until the bind exists, the smoke runs in **offline-target mode**: the
# worker commits locally, and the exit-gate inspects /rig/smoke-target/.git
# instead of the host bare repo. Hetzner driver doesn't currently support
# pushing back to host either — flagged in engdocs/cloud-smoke.md
# "Known limitations".
echo "offline" > "${SMOKE_STATE_DIR}/target_mode"

# Strip per-host git config from the working clone before seeding the
# PVC so the in-pod git uses container-local identity.
( cd "$work" && git remote remove origin || true )

kubectl_cp_rig "$work" "smoke-target"
log "rig seeded at /rig/smoke-target with bead ${issue_id}"
