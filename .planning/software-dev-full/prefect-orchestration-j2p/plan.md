# Plan: prefect-orchestration-j2p — k8s/Docker work-pool support

## Affected files

**Core (`prefect-orchestration/`)**
- `Dockerfile` *(new)* — multi-stage image bundling `uv` + `bd` + `claude` CLI + `prefect-orchestration` + a configurable formula pack (via `--build-arg PACK_SPEC=...`).
- `docker-compose.yml` *(new)* — local dev stack: `prefect-server`, `prefect-worker`, optional bind-mounted rig at `/rig`, optional `~/.claude/.credentials.json:ro` mount.
- `prefect_orchestration/doctor.py` — add `check_deployment_pools_exist()` and append to `ALL_CHECKS`.
- `engdocs/work-pools.md` *(new)* — k8s + docker work-pool playbook, rig-state strategy decision, container backend behavior, OAuth caveat, image rebuild cadence.
- `README.md` — one-paragraph pointer to `engdocs/work-pools.md`.
- `CLAUDE.md` — add a "Containerized runs" subsection under "Common workflows" (3–5 lines, link to engdocs).
- `tests/test_doctor.py` — unit test for the new pool-existence check (mock `read_work_pools` + `load_deployments`).
- `scripts/smoke-compose.sh` *(new, optional)* — one-command driver: `docker compose up -d`, exec into worker, `po run software-dev-full --issue-id <demo>`. Documented as the "local docker-compose smoke" AC.

**Pack (`../software-dev/po-formulas/`)** — pack-side changes per CLAUDE.md "land pack-contrib code in the pack's repo":
- `po_formulas/deployments.py` — extend the example `register()` so a deployment can opt into a non-process pool via `PO_DEFAULT_WORK_POOL` env (defaults to unset → server picks). Comment block showing `work_pool_name="po-k8s"`.

**Out-of-scope deferrals (called out in docs, not implemented):**
- Multi-tenant rig isolation, OAuth secret management for k8s pods (sibling beads), ephemeral clone+push (blocked on git remote + Dolt server mode).

## Approach

1. **Dockerfile.** Multi-stage:
   - Stage `tools`: alpine/debian-slim base; `curl` install of `uv`, `claude` (Node-based — install node:20-slim then `npm i -g @anthropic-ai/claude-code`), and `bd` (Go binary via release tarball).
   - Stage `runtime`: copy the three binaries into a slim Python 3.13 image; `uv tool install prefect-orchestration` (or `--editable` from a build context for dev); accept `ARG PACK_SPEC=po-formulas-software-dev` and `uv tool install --with $PACK_SPEC ...` so the same Dockerfile builds dev and formula-specific images.
   - `WORKDIR /rig`; `ENV PREFECT_API_URL=http://prefect-server:4200/api PO_BACKEND=cli`. No `tmux` installed → backend auto-fall-back kicks in (already implemented in `software_dev.py:441` via `shutil.which("tmux")`); we explicitly set `PO_BACKEND=cli` to make the choice loud and prevent surprises.
   - Final `CMD ["prefect", "worker", "start", "--pool", "po"]`.

2. **docker-compose.yml.** Three services: `prefect-server` (official `prefecthq/prefect:3-latest` running `prefect server start`), `worker` (built from local `Dockerfile`, depends_on server, mounts `./rig:/rig` and `${HOME}/.claude/.credentials.json:/root/.claude/.credentials.json:ro`), and an optional `client` profile that runs `po run` interactively. Documented OAuth credential mount as the explicit short-term path (per triage Risks).

3. **`po doctor` pool check.** New function `check_deployment_pools_exist()`:
   - Loads `_deployments.load_deployments()`; if no deployment has a `work_pool_name`, returns `OK` with `"no pool-bound deployments"`.
   - Otherwise calls `client.read_work_pools()` once, builds a set of names, and checks each deployment's `work_pool_name` against it.
   - Missing pools → `WARN` (not `FAIL` — many users `po deploy` without applying), with remediation `prefect work-pool create <name> --type process|kubernetes|docker`.
   - Skipped (not failed) when `PREFECT_API_URL` unset, mirroring `check_work_pool_exists()`.
   - Append to `ALL_CHECKS` after `check_work_pool_exists` so it slots into the existing render order.

4. **Backend behavior in containers.** No code change to `agent_session.py` — `software_dev.py:441` already does `TmuxClaudeBackend if shutil.which("tmux") else ClaudeCliBackend`, and `tmux` is intentionally absent from the image. The `PO_BACKEND=tmux` hard-error path (line 437–438) is preserved. `engdocs/work-pools.md` documents that `PO_BACKEND` is unset by default in the image and `=cli` is forced; `=tmux` will error loudly inside a pod (intentional).

5. **Pack `register()` example.** Show how a pack opts a deployment into `po-k8s`:

   ```python
   def register():
       pool = os.environ.get("PO_DEFAULT_WORK_POOL")  # e.g. "po-k8s"
       deps = [
           epic_run.to_deployment(name="nightly", schedule=Cron("0 9 * * *")),
       ]
       if pool:
           for d in deps:
               d.work_pool_name = pool
       return deps
   ```

   Two-line change with comment explaining: tests stay process-pool by default; CI/prod sets `PO_DEFAULT_WORK_POOL=po-k8s` before `po deploy --apply`.

6. **Rig-state decision.** Document in `engdocs/work-pools.md`: short-term, use a **bind-mounted rig** for compose and an **RWX PVC** for k8s (single-writer per epic via `bd` claim guarantees). Defer ephemeral clone+push to a sibling bead; cite (a) no git remote on this repo and (b) `bd` Dolt server-mode prereq.

7. **README + CLAUDE.md.** Both stay short. README adds a "Containerized runs" link; CLAUDE.md adds a 3–5 line subsection under "Common workflows" with the `docker compose up`/smoke command.

## Acceptance criteria *(verbatim from issue)*

> Documented k8s + docker work-pool path; local docker-compose smoke runs one issue through software-dev-full in a container; `po doctor` warns on missing pool

Decomposed:
- **AC1.** Documented path exists for running `po run software-dev-full` against a k8s pool end-to-end (`engdocs/work-pools.md` covers k8s pool create, image build/push, deployment apply with `work_pool_name`, worker start, manual run trigger).
- **AC2.** Local docker-compose smoke runs one issue through `software-dev-full` in a container (`docker-compose.yml` + `scripts/smoke-compose.sh` + a documented dry-run via `PO_BACKEND=stub` so the smoke does not require a Claude OAuth credential to demonstrate the wiring).
- **AC3.** `po doctor` warns on a deployment whose `work_pool_name` references a non-existent pool.

## Verification strategy

- **AC1** — manual review of `engdocs/work-pools.md` for completeness (k8s section: `prefect work-pool create … --type kubernetes`, image push, `work_pool_name` set on register, `prefect worker start --pool po-k8s`, `prefect deployment run`). Plan critic verifies.
- **AC2** — `docker compose build && docker compose up -d && docker compose exec worker env PO_BACKEND=stub po run software-dev-full --issue-id <demo> --rig demo --rig-path /rig`. Stub backend short-circuits Claude calls but exercises full Prefect flow + bd shell-out wiring. Captured as `scripts/smoke-compose.sh` and run manually during build/verify; not added to CI in this issue.
- **AC3** — unit test `tests/test_doctor.py::test_check_deployment_pools_exist_warns_on_missing_pool` mocks `_deployments.load_deployments()` to return a deployment whose `work_pool_name="ghost"` and patches `prefect.client.orchestration.get_client` to return a stub yielding pools `[Pool(name="po")]`. Asserts result has `Status.WARN` and message includes `"ghost"`. Companion test for the all-pools-present `OK` case and the no-pool-bound-deployments path.

## Test plan

- **Unit** — primary: 3 cases in `tests/test_doctor.py` (warn-on-missing, ok-when-present, no-deployments-pinned).
- **e2e** — none added by this issue. The compose smoke is a manual repro script (AC2 path), not a `pytest` e2e — running Prefect+Claude in a container in pytest is flaky for marginal value, and the unit-test coverage of the doctor logic is sufficient. The existing `tests/e2e/test_po_doctor_cli.py` continues to exercise the doctor command surface.
- **Playwright** — N/A (no UI).

## Risks

- **Image bloat / build time.** Bundling Node (claude CLI) + Go binary (bd) + uv-managed Python pulls ~500MB even after multi-stage. Mitigation: pin versions, `.dockerignore`, document a `--target tools` partial build for fast rebuilds. Not blocking AC.
- **Claude OAuth in the container.** Bind-mounting `~/.claude/.credentials.json` works on a developer laptop but is unsuitable for k8s. Documented as a known limitation; the smoke uses `PO_BACKEND=stub` so AC2 doesn't require it. The k8s OAuth path is an explicit out-of-scope sibling bead per the issue.
- **Doctor check noise.** Most users won't pin `work_pool_name`. The check returns `OK` ("no pool-bound deployments") in that case to avoid yellowing every `po doctor` run. Verified via the dedicated unit test.
- **Backend silent fallback.** `software_dev.py:441` falls back to `ClaudeCliBackend` when tmux is missing — already correct in core. Risk is only that someone reads pod logs and is surprised; mitigated by explicit `ENV PO_BACKEND=cli` in the Dockerfile and a paragraph in the engdocs.
- **Concurrency tags across pools.** Per-role `prefect concurrency-limit` is global; verify in the engdocs (no code change), one paragraph noting that `builder`/`critic` tag limits remain in effect when tasks run on a non-process pool. No risk to existing behavior.
- **No API contract change.** `RunnerDeployment.work_pool_name` is already settable; we add a doctor read, not a write. No breaking consumers.
- **Migrations.** None (no DB schema changes).
- **Pack repo changes.** The `register()` tweak is additive — defaults preserve current behavior. Existing `po deploy` users see no change unless they set `PO_DEFAULT_WORK_POOL`.
