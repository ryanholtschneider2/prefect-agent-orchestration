# Plan: prefect-orchestration-j2p — k8s/Docker work-pool support

## Affected files

**Core (`prefect-orchestration/`)**
- `Dockerfile` *(rewrite)* — base image moves from `python:3.13-slim` to
  `ubuntu:24.04` per triage. Bundles `node 22` + `@anthropic-ai/claude-code`
  + `tmux` + `git` + `uv` + `bd` + `prefect-orchestration`; non-root
  `coder` user; entrypoint that bootstraps `~/.claude.json` from
  `ANTHROPIC_API_KEY` (modeled on `~/Desktop/Code/rclaude/entrypoint.sh`).
- `Dockerfile.pack` *(new)* — per-pack overlay: `FROM po-worker:base` +
  `RUN uv tool install --with <pack>` so adding a pack to an image is one
  build step. Documented as the per-pack composition shape.
- `docker/entrypoint.sh` *(new)* — runs at container start: writes
  `~/.claude.json` + `~/.claude/settings.json` (skip onboarding, trust
  /workspace + /rig, accept API-key suffix), then `exec`s the supplied
  command (default: `prefect worker start --pool po`).
- `docker-compose.yml` *(rewrite)* — three services: `prefect-server`,
  `worker` (built from local Dockerfile, mounts `./rig:/rig`), `client`
  (interactive driver). `ANTHROPIC_API_KEY` from host env; OAuth bind
  mount kept as commented-out fallback.
- `k8s/po-worker-deployment.yaml` *(new)* — Deployment + ServiceAccount
  for a Prefect worker pod targeting the `po-k8s` pool. Mounts a
  `Secret` (`anthropic-api-key`) + a `PersistentVolumeClaim` (`po-rig`,
  RWX). Single replica; scaling guidance in the engdocs.
- `k8s/po-base-job-template.json` *(new)* — Prefect work-pool
  base-job-template: pulls `po-worker:dev`, mounts the same Secret +
  PVC, sets `PO_BACKEND=cli`, no TTY.
- `k8s/po-rig-pvc.yaml`, `k8s/anthropic-api-key.example.yaml` *(new)* —
  the PVC + a Secret template with `stringData` placeholder. Real secret
  is `kubectl create secret generic anthropic-api-key …` from a CI step;
  the YAML is for documentation only.
- `prefect_orchestration/agent_session.py` — add an explicit
  `select_default_backend()` helper that returns `TmuxClaudeBackend`
  only when both `tmux` is on PATH **and** `sys.stdout.isatty()` is true;
  else `ClaudeCliBackend`. Pack code keeps its env-var override; this
  helper centralizes the headless detection so non-pack callers (tests,
  future scripts) get the same behavior.
- `prefect_orchestration/doctor.py` — `check_deployment_pools_exist()`
  already lives here from the prior attempt; verify it still meets the
  triage spec (WARN on missing pool, OK when no pinned deps, skipped
  when API URL absent) and ensure it is in `ALL_CHECKS`. No-op if
  unchanged.
- `engdocs/work-pools.md` *(rewrite)* — full playbook: image build,
  per-pack overlay, k8s manifests + `prefect work-pool create --type
  kubernetes --base-job-template …`, secret + PVC layout, OAuth-vs-API
  decision, rig-state strategy, backend selection, `po doctor` check,
  concurrency.
- `README.md` — short pointer paragraph to `engdocs/work-pools.md`.
- `CLAUDE.md` — short subsection under "Common workflows".
- `tests/test_doctor.py` — keep the pool-existence cases (already
  passing); add a regression guard that the new `select_default_backend`
  helper falls back to `ClaudeCliBackend` when stdout is non-TTY.
- `tests/test_agent_session.py` (or extend existing) — unit tests for
  `select_default_backend()`: tmux+TTY → tmux; tmux+pipe → cli; no tmux
  → cli; `PO_BACKEND=tmux` without tmux → raises.
- `scripts/smoke-compose.sh` — keep; default to `PO_BACKEND=stub` so the
  smoke does not need `ANTHROPIC_API_KEY`. `PO_BACKEND=cli` requires the
  env var be exported.
- `.dockerignore` — already exists; add `k8s/` so the worker image
  doesn't bake in cluster manifests.

**Pack repos (sibling, not in this rig)**
- `software-dev/po-formulas/po_formulas/deployments.py` — *future
  follow-up*. Plan documents the shape (`PO_DEFAULT_WORK_POOL` env →
  `register()` sets `work_pool_name` on each deployment) but does NOT
  edit the sibling repo. Per CLAUDE.md "land pack-contrib code in the
  pack's repo".

**Out of scope (called out, not implemented)**
- Multi-tenant rig isolation, ephemeral clone+push (blocked on git
  remote + bd Dolt server-mode), billing telemetry, OAuth secret
  management for k8s pods (workers use `ANTHROPIC_API_KEY`).

## Approach

1. **Base image rewrite (`Dockerfile`).** Switch to `ubuntu:24.04` so we
   inherit the rclaude toolchain shape — Claude Code refuses
   `--dangerously-skip-permissions` as root, so a non-root `coder` user
   is required. Multi-stage:
   - `tools` stage: install `uv` (Astral installer), `bd` (gastownhall
     v1.0.3 release tarball — the prior attempt used the right URL but a
     bad tag; pin via `ARG BD_VERSION=1.0.3`).
   - `base` stage: ubuntu:24.04 + apt(`curl ca-certificates git
     openssh-client gnupg sudo jq tmux`) + Node 22 (NodeSource); copy
     `uv` and `bd` from `tools`; `npm i -g @anthropic-ai/claude-code`;
     create `coder` user; `pip install` (or `uv tool install`)
     `prefect-orchestration` from the build context. **Tmux IS installed**
     — pods may run with TTY allocated when humans `kubectl exec` for
     debugging; absence of TTY is detected at runtime by
     `select_default_backend()`. `ENV PO_BACKEND=cli` is still set so the
     auto-fall-back is loud, not implicit.
   - Final `CMD ["prefect","worker","start","--pool","po"]`. Entrypoint
     handles Claude bootstrap before `exec`ing the command.

2. **Per-pack overlay (`Dockerfile.pack`).** A trivial second Dockerfile:
   ```
   ARG BASE=po-worker:base
   FROM ${BASE}
   ARG PACK_SPEC
   RUN uv tool install --with "${PACK_SPEC}" prefect-orchestration
   ```
   Documented as the recommended composition shape for pack-specific
   images (`po-worker:software-dev`, `po-worker:rocks-geo`, etc.). Local
   sibling-repo path stays available via `--build-context pack=…` (kept
   from the prior attempt — its `FROM scratch AS pack` default trick
   means `--build-context` is optional).

3. **Entrypoint (`docker/entrypoint.sh`).** Modeled directly on
   `~/Desktop/Code/rclaude/entrypoint.sh:75-105`. Writes
   `~/.claude.json` with `hasCompletedOnboarding`,
   `hasAcknowledgedCustomApiKey`, `bypassPermissionsModeAccepted`,
   `customApiKeyResponses.approved` containing the last 20 chars of
   `$ANTHROPIC_API_KEY`, and trust entries for `/workspace` + `/rig`.
   Then `exec "$@"` so the container's CMD (`prefect worker start …`)
   becomes PID 1's child. If `ANTHROPIC_API_KEY` is unset and
   `PO_BACKEND` is not `stub`, error loudly with a clear message.

4. **Compose (`docker-compose.yml`).** Mostly the existing file with
   two changes: (a) drop the unconditional OAuth credential bind-mount
   from the `worker` service — replace with `environment:
   ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY}` so
   compose refuses to start without the key; (b) add a commented-out
   `~/.claude/.credentials.json:ro` mount as the OAuth fallback the
   triage explicitly mentions.

5. **k8s manifests + base-job-template.** Three small YAMLs (Deployment,
   PVC, Secret stub) plus one JSON base-job-template. The Deployment
   runs `prefect worker start --pool po-k8s`; the base-job-template tells
   Prefect how to launch each flow as a Job (image, secret mount, PVC
   mount, env). All under `k8s/` so users can `kubectl apply -f k8s/`.

6. **`select_default_backend()`.** Add to `agent_session.py`:
   ```python
   def select_default_backend() -> type[SessionBackend]:
       if shutil.which("tmux") and sys.stdout.isatty():
           return TmuxClaudeBackend
       return ClaudeCliBackend
   ```
   Existing pack code in `software_dev.py:441` already does
   `shutil.which("tmux")` — the helper hardens it by also requiring a
   TTY. Pack's `PO_BACKEND` env override path keeps working unchanged.
   Recommend (in the engdocs) that future packs use this helper instead
   of duplicating the check.

7. **`po doctor` pool check.** Verified present from a prior iter
   (`check_deployment_pools_exist` in `doctor.py`, in `ALL_CHECKS`,
   tested in `tests/test_doctor.py`). No code change unless the existing
   tests now fail. If they do, fix to match the spec: WARN on missing
   pool, OK when no deployment pins a pool, skipped when API URL absent.

8. **Docs.** `engdocs/work-pools.md` is the single source of truth.
   Sections: `Image build` (base + per-pack overlay), `Local docker
   compose`, `Kubernetes` (image push, pool create, manifests apply,
   secret + PVC, worker start, deployment trigger), `Backend selection`
   (TTY detection + `PO_BACKEND` override), `Auth` (API key, OAuth
   fallback for laptop dev), `Rig state` (PVC/bind-mount now, ephemeral
   deferred), `Concurrency`, `Doctor checks`. README + CLAUDE.md gain
   3-line pointers.

## Acceptance criteria *(verbatim from issue)*

> A documented path to run po run software-dev-full against a k8s pool
> end-to-end; local docker-compose smoke that runs one issue through
> software-dev-full in a container; po doctor surfaces missing pool /
> image misconfig

Decomposed:
- **AC1.** End-to-end k8s path documented:
  `engdocs/work-pools.md` covers image build, push, `prefect work-pool
  create --type kubernetes --base-job-template`, secret + PVC creation,
  Deployment apply, `prefect deployment run` trigger.
- **AC2.** Local docker-compose smoke runs one issue through
  `software-dev-full` in a container. `scripts/smoke-compose.sh` brings
  the stack up, ensures the pool exists, and runs `po run
  software-dev-full --issue-id <demo>` against the worker. Default
  `PO_BACKEND=stub` so the smoke is deterministic and does not require
  an Anthropic API key; `PO_BACKEND=cli` flips on real Claude with the
  exported key.
- **AC3.** `po doctor` warns on a deployment whose `work_pool_name`
  references a non-existent pool (existing
  `check_deployment_pools_exist`).

## Verification strategy

- **AC1** — the engdocs file is reviewable end-to-end. Plan critic
  reads it; verifier checks for: (a) `prefect work-pool create … --type
  kubernetes --base-job-template k8s/po-base-job-template.json`, (b)
  Secret + PVC apply commands, (c) image push step, (d) `prefect
  deployment run` example. No live cluster required to verify — text
  presence + manifest correctness is the bar.
- **AC2** — execute `./scripts/smoke-compose.sh` against a fresh `./rig`
  with `bd init` already run and at least one open beads issue.
  Capture stdout/stderr to
  `.planning/software-dev-full/prefect-orchestration-j2p/smoke-output.txt`.
  Pass criterion: the script exits 0 and `verdicts/triage.json` (or any
  step's verdict file) appears in
  `./rig/.planning/software-dev-full/<issue>/`.
- **AC3** — unit tests in `tests/test_doctor.py` already cover three
  cases (warn on missing, ok when present, no pinned deployments).
  Re-run `uv run python -m pytest tests/test_doctor.py -q`; all three
  must pass. Plus a manual `po doctor` run against a server with one
  pack-declared pool-pinned deployment whose pool was never created —
  expected: yellow row, exit 0 (warn ≠ fail).

## Test plan

- **Unit** — `tests/test_doctor.py` (existing, 3 cases for the pool
  check); new `tests/test_agent_session.py` cases for
  `select_default_backend()` (tmux+tty, tmux+pipe, no-tmux,
  PO_BACKEND=tmux without tmux raises).
- **e2e** — none added in this issue. The compose smoke is a manual
  repro script (AC2 path), not a `pytest e2e` — running Prefect+Claude
  in a container in pytest is too flaky for the marginal value, and
  `tests/e2e/test_po_doctor_cli.py` keeps exercising the doctor command
  surface. The smoke is captured as a one-off output artifact during
  builder verify.
- **Playwright** — N/A (no UI).

## Risks

- **Image bloat.** ubuntu:24.04 + node22 + tmux + Claude Code + uv +
  bd + Python ~ 800-1000 MB. Mitigation: keep multi-stage so the
  `tools` stage caches; partial `--target tools` for fast doc
  iterations. Not blocking AC.
- **API-key handling.** `ANTHROPIC_API_KEY` in the worker conflicts
  with the user-global rule "never use API keys for local script
  execution / dev workflows" — but worker pods *are* deployed services,
  so the project decision overrides. The compose `client` profile keeps
  an OAuth bind-mount path commented out for laptop dev that prefers
  the subscription. Documented in `engdocs/work-pools.md` § "Auth".
- **Claude Code root refusal.** Without the entrypoint bootstrap (or
  running as a non-root `coder` user), Claude Code refuses
  `--dangerously-skip-permissions` and hangs on the trust dialog.
  Mitigated by the `coder` user in the Dockerfile + the entrypoint's
  `~/.claude.json` write. The smoke catches this immediately if it
  regresses (Claude won't print `--version`).
- **Tmux fall-back correctness.** If `select_default_backend()` is
  added but the pack still does its own check, two checks coexist —
  acceptable, since they agree. If a future pack imports the helper
  but a third path bypasses it, behavior could diverge. Mitigation:
  document the helper as the canonical path; tests pin the headless
  fallback. Not a breaking risk because the existing pack logic is
  unchanged.
- **`po doctor` noise.** Returning OK when no deployment pins a pool
  keeps the table quiet on fresh installs. Verified by the existing
  `test_deployment_pools_no_pinned_deployments` test.
- **Cross-rig PO_BACKEND default.** The compose `worker` service hard-
  sets `PO_BACKEND=cli`; the `client` service defaults to `stub` so
  the smoke doesn't require a key. Consumers building their own compose
  files inherit the worker default — documented prominently.
- **Migrations.** None (no DB schema change in either Prefect or bd).
- **API contract.** No change. `RunnerDeployment.work_pool_name` was
  always settable; we only add a doctor read and a documented pattern
  for `register()`.
- **Pack-side change deferred.** The `register()` example using
  `PO_DEFAULT_WORK_POOL` is documented in the engdocs but not committed
  in the sibling pack repo. A trailing follow-up bead in the pack repo
  picks that up.
