# Decision log: prefect-orchestration-j2p

## Build iter 1

- **Decision**: Factored `_read_pool_names()` out of `check_work_pool_exists`
  rather than duplicating the `get_client()` / `read_work_pools()` block.
  **Why**: Two checks now need pool names (existing + new
  `check_deployment_pools_exist`); a single seam keeps the network call
  to one per `po doctor` and gives tests a clean monkeypatch target.
  **Alternatives considered**: stub `prefect.client.orchestration.get_client`
  directly in tests (more invasive); duplicate the asyncio block (lint
  noise + two timeouts on a slow server).

- **Decision**: New doctor check returns `WARN`, not `FAIL`, on missing
  pools.
  **Why**: Plan §3 + Risks §"Doctor check noise" — most users iterate
  on `po deploy` without `--apply`, so a hard fail would yellow every
  doctor run on a fresh laptop. Pool absence is a misconfig, not a
  broken install.
  **Alternatives considered**: `FAIL` (too noisy); silently OK (defeats
  the point of the check).

- **Decision**: Doctor check returns `OK` (not skipped) when no
  deployment pins a pool.
  **Why**: The check has nothing to validate in that case; emitting
  `WARN` would punish packs that don't use pool pinning. `OK` keeps
  the table row stable.
  **Alternatives considered**: omit the row when no pins exist
  (would surprise users grepping doctor output for the check name).

- **Decision**: Dockerfile copies `node` + `npm` + `node_modules` from
  the `tools` stage rather than installing Debian's `nodejs` package
  in `runtime`.
  **Why**: Debian's package pulls ~150 MB of dependencies and has a
  much older Node version than the `node:20-slim` toolchain image.
  Copying the binaries keeps version control with the build-arg.
  **Alternatives considered**: `FROM node:20-slim` for the runtime
  stage (Python install becomes much heavier); rely on a sidecar
  container for claude (fragments OAuth handling).

- **Decision**: `ENV PO_BACKEND=cli` baked into the runtime stage
  instead of relying on the auto-fall-back when tmux is absent.
  **Why**: Plan §4 — make the choice loud. Anyone reading pod logs
  sees `cli` selected without inferring from `shutil.which("tmux")`.
  Auto-fall-back still works as the safety net, but the explicit env
  removes ambiguity in incident review.
  **Alternatives considered**: leave `PO_BACKEND` unset (relies on
  implicit pack logic); install tmux in the image (no TTY in pods,
  pointless).

- **Decision**: Smoke script defaults to `PO_BACKEND=stub`.
  **Why**: AC2 says "smoke runs one issue through software-dev-full
  in a container" — stub backend exercises Prefect + bd wiring without
  requiring Claude OAuth. This means AC2 can be verified without the
  out-of-scope OAuth-in-container work. Plan §AC2 explicitly noted this.
  **Alternatives considered**: require `PO_BACKEND=cli` and a mounted
  credential file (couples the smoke to a host setup that compose
  alone can't guarantee).

- **Decision**: Did NOT modify `prefect_orchestration/agent_session.py`.
  **Why**: Plan §4 — the pack already does
  `TmuxClaudeBackend if shutil.which("tmux") else ClaudeCliBackend`
  in `software-dev/po-formulas/po_formulas/software_dev.py:441`,
  which is exactly the desired behavior in containers. Adding a
  duplicate TTY check in core would violate single-source-of-truth.
  **Alternatives considered**: add `isatty()` detection in core
  (redundant, two places to maintain).

- **Decision**: Did NOT touch `software-dev/po-formulas/po_formulas/deployments.py`
  in this iter.
  **Why**: That pack is a sibling repo, not in this rig's worktree.
  Plan §1 lists it under "Pack" but CLAUDE.md says core changes ship
  in core; pack-side `register()` example is documented in
  `engdocs/work-pools.md` instead so users see the pattern even
  before the pack repo lands its tweak. The pack change is a 2-line
  follow-up that can ride a separate bead in the pack repo.
  **Alternatives considered**: `cd ../software-dev/po-formulas` from
  the builder (cross-repo edits without a bead in that repo would be
  hard to review).

- **Decision**: No new e2e test for the compose smoke.
  **Why**: Plan §"Test plan" — running Prefect+Claude in a container
  inside pytest is flaky for marginal value, and the doctor check
  has unit coverage. The smoke is a manual repro script (AC2 path),
  documented in engdocs.
  **Alternatives considered**: add a `tests/e2e/test_compose_smoke.py`
  that invokes the script (would require docker daemon in CI, which
  this repo doesn't have).
