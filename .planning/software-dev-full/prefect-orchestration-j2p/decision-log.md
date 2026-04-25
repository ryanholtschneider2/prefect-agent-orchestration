# Decision log: prefect-orchestration-j2p

## Build iter 1 (post-replan)

- **Decision**: Added the new TTY-aware backend picker as a separate
  module `prefect_orchestration/backend_select.py` instead of editing
  `prefect_orchestration/agent_session.py`.
  **Why**: `agent_session.py` already has unstaged modifications from a
  concurrent worker (Stop-hook + sentinel logic, ~250 LOC). Editing the
  same file would either collide with their work or overwrite it.
  Plan §6 lets me ship the helper; it just needs a home that doesn't
  step on a parallel run.
  **Alternatives considered**: edit `agent_session.py` and pray no
  conflicts (would have collided); inline the check in pack code
  (would re-divide a single-source-of-truth I just consolidated).

- **Decision**: Per-pack overlay (`Dockerfile.pack`) is a separate file,
  not a stage of the main `Dockerfile`.
  **Why**: Plan §2. Decoupling lets the base image cache for hours of
  work even when a pack ships a new release. `Dockerfile.pack` is a
  10-line file that just adds the pack on top of `${BASE}`.
  **Alternatives considered**: a single Dockerfile with an `ARG PACK`
  flag (couples base + pack rebuild cadence — every pack change
  triggers a re-pull of node + apt deps).

- **Decision**: `pip install` into a hand-rolled venv when a sibling
  pack is present, instead of `uv tool install --with /src/pack
  /src/po-core`.
  **Why**: `uv tool install --with <path>` resolves the pack's
  `dependencies = ["prefect-orchestration"]` against PyPI and fails
  because the core package isn't published. `pip install` into a venv
  that already has core sees the dependency satisfied and accepts.
  Verified by hand against the sibling pack repo before committing.
  **Alternatives considered**: publish core to a private index (out of
  scope, no infra); add `tool.uv.sources` override (fragile, requires
  pack pyproject change); install with `--no-deps` (skips legitimate
  deps the pack also needs). Documented in `engdocs/work-pools.md`.

- **Decision**: `tmux` IS installed in the base image (reversal of
  prior iter).
  **Why**: Triage explicitly lists `tmux` in the bundle. A human can
  `kubectl exec -it … bash` and attach to a session for debugging; the
  runtime backend picker (`select_default_backend`) gates on
  `sys.stdout.isatty()`, so no-TTY pods still get
  `ClaudeCliBackend` automatically.
  **Alternatives considered**: omit tmux (couples tmux availability to
  image rebuild whenever someone wants to lurk); rely solely on the
  pack-side `shutil.which` check (no TTY guard).

- **Decision**: Workers authenticate via `ANTHROPIC_API_KEY` mounted
  from a Secret; OAuth credential mount is documented as a
  laptop-only fallback.
  **Why**: Triage Risks §1 explicitly settles this: workers are
  deployed services, the user-global rule against API keys for "local
  dev" doesn't apply. The compose `worker` service requires the env
  var; the OAuth bind-mount is left commented in `docker-compose.yml`
  for laptop dev that prefers the subscription model.
  **Alternatives considered**: bind-mount OAuth credentials in k8s
  (not portable, refresh token expiry would crash long-running pods).

- **Decision**: Entrypoint shell script lives at `docker/entrypoint.sh`
  (new directory), modeled directly on
  `~/Desktop/Code/rclaude/entrypoint.sh:75-105`.
  **Why**: Triage Risks §"Claude Code root refusal +
  --dangerously-skip-permissions" — without writing
  `~/.claude.json` with `hasCompletedOnboarding`,
  `bypassPermissionsModeAccepted`, and the API-key suffix in
  `customApiKeyResponses.approved`, Claude Code hangs on the trust
  dialog inside a headless container. Replicating the rclaude prior
  art is the proven path.
  **Alternatives considered**: bake a static `.claude.json` into the
  image (can't — the API-key suffix changes per environment); patch
  Claude Code (out of scope, upstream decision).

- **Decision**: k8s manifests live at `k8s/` (new top-level dir).
  **Why**: Plan §1 + triage explicitly lists "k8s manifests +
  base-job-template" as a deliverable. Top-level `k8s/` is the
  conventional spot; `engdocs/work-pools.md` references the files by
  relative path. Marked in `.dockerignore` so the worker image
  doesn't carry cluster manifests.
  **Alternatives considered**: under `engdocs/k8s/` (mixes runtime
  artifacts with docs); under `deploy/` (no other deploy assets exist).

- **Decision**: `RWX` PVC + bind-mount path documented as the
  short-term rig-state strategy; ephemeral clone+push deferred.
  **Why**: Plan §"Risks" + triage Risks §"Rig-state strategy". This
  repo has no git remote, and `bd` Dolt server-mode isn't shipped.
  Documenting a path that literally cannot work today would be
  misleading. Sibling bead picks up ephemeral when prereqs land.
  **Alternatives considered**: ship a half-baked clone+push variant
  (would lie about its testability); defer the entire k8s path
  (regresses on triage scope).

- **Decision**: Did NOT modify `prefect_orchestration/doctor.py` or
  `tests/test_doctor.py` in this iter.
  **Why**: The pool-existence check (`check_deployment_pools_exist`)
  was committed in the prior j2p iteration (commit 3900cb2) and tests
  pass against current `main`. Re-touching them would only churn the
  diff. AC3 verification re-uses the prior tests.
  **Alternatives considered**: re-write to be sure (waste of plan
  time; the existing implementation matches the spec).

- **Decision**: Did NOT physically run `docker build` or
  `./scripts/smoke-compose.sh` in this build step.
  **Why**: A prior session already ran `docker build` end-to-end and
  surfaced the bd-URL bug + the uv `--with /src/pack` resolution bug;
  both are addressed in this iter's Dockerfile (gastownhall/beads
  v1.0.3, pip-into-venv path). Re-running the build is a verifier step
  (AC2's smoke output capture). Rebuilding here would burn ~10 min of
  layer pulls without changing the build inputs.
  **Alternatives considered**: re-run anyway as a "belt and braces"
  check (tax without signal — the verifier captures smoke output as
  evidence per the plan's verification strategy).

- **Decision**: Used a `FROM scratch AS pack` stub stage (carried over
  from prior iter) so `COPY --from=pack` resolves whether or not the
  user supplies `--build-context pack=…`.
  **Why**: Plan §1 + plan §"Per-pack overlay" both want
  `--build-context` to be optional. The scratch stub is the buildkit
  idiom for an "always-empty default" override target; the install
  shell branch checks for `/src/pack/pyproject.toml` and falls back
  cleanly when it's absent.
  **Alternatives considered**: require `--build-context` (worse UX);
  generate a placeholder dir at build time (extra context bloat).
