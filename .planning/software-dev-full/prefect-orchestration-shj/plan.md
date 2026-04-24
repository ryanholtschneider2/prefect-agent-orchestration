# Plan: prefect-orchestration-shj — `po deploy`

## Affected files

- `prefect_orchestration/cli.py` — add `deploy` subcommand; factor entry-point loader into a shared helper (or add a sibling `_load_deployments()`).
- `prefect_orchestration/deployments.py` *(new)* — discovery + listing + apply logic for the `po.deployments` entry-point group. Keeps CLI thin; easier to unit-test.
- `README.md` — document `[project.entry-points."po.deployments"]`, the `register()` convention, and `po deploy` / `po deploy --apply`.
- `../../software-dev/po-formulas/pyproject.toml` — register a `po.deployments` entry point (e.g. `software-dev = "po_formulas.deployments:register"`).
- `../../software-dev/po-formulas/po_formulas/deployments.py` *(new)* — example `register()` returning a nightly 09:00 `epic_run` deployment for `epic-sr-8yu`.
- `tests/test_deployments.py` *(new)* — unit tests for discovery + listing; stub the Prefect side for `--apply` smoke.

## Approach

Mirrors the existing `po.formulas` pattern (see `prefect_orchestration/cli.py:34-47`). A pack ships a `register()` callable under the `po.deployments` entry-point group; each callable returns one or more Prefect `Deployment` objects (or a `RunnerDeployment` built by `flow.to_deployment(...)`). Core never knows about specific flows.

### Convention

```python
# in a pack
from prefect import flow
from prefect.schedules import Cron  # Prefect 3 schedule helpers

def register() -> list:
    return [
        epic_run.to_deployment(
            name="epic-sr-8yu-nightly",
            schedule=Cron("0 9 * * *", timezone="America/New_York"),
            parameters={"epic_id": "sr-8yu", "rig": "site", "rig_path": "./site"},
        ),
    ]
```

`register()` may return a single deployment or a list; core normalizes to a list. `RunnerDeployment` is Prefect's native, in-process-agnostic representation — compatible with both `flow.serve` (local/process runner) and `flow.deploy` (work-pool-backed).

### Discovery (`deployments.load_deployments()`)

1. Iterate `importlib.metadata.entry_points(group="po.deployments")` with the same fallback as `_load_formulas`.
2. For each EP, call the loaded callable with no args; wrap exceptions in a `typer.echo(... err=True)` warning so one bad pack doesn't mask others.
3. Flatten results into `list[(pack_name, deployment)]`.

### `po deploy` (list mode)

Default (no flags): print a table — pack name, deployment name, flow name, schedule summary (`Cron(...)`, `Interval(...)`, or `manual`), parameters keys. No side effects.

### `po deploy --apply`

For each deployment object, call `deployment.apply()` (Prefect 3 `RunnerDeployment.apply()` upserts by `flow_name/name`, returns deployment ID — idempotent by design). Requires a running Prefect server/cloud at `PREFECT_API_URL`; if unset, warn and exit 2. Manual-trigger deployments (no schedule) still apply — they're created without a schedule, exactly what Prefect expects for UI/API-triggered runs.

`--apply` implies a Prefect server call, so network/auth errors are surfaced plainly with the deployment name; one failure doesn't abort the rest (continue + non-zero exit if any failed).

Optional flags:
- `--pack <name>` filter.
- `--name <deployment-name>` filter.
- `--work-pool <pool>` override to set `work_pool_name` on each deployment before apply (Prefect expects this for `deploy`-style registration; `to_deployment` alone is fine for `serve`, but for a server-registered deployment that a worker can pick up, `apply()` needs a work pool).

### Example pack content

New `po_formulas/deployments.py`:

```python
from prefect.schedules import Cron
from po_formulas.epic import epic_run

def register():
    return [
        epic_run.to_deployment(
            name="epic-sr-8yu-nightly",
            schedule=Cron("0 9 * * *", timezone="America/New_York"),
            parameters={"epic_id": "sr-8yu"},
        ),
    ]
```

And in `po-formulas/pyproject.toml`:

```toml
[project.entry-points."po.deployments"]
software-dev = "po_formulas.deployments:register"
```

### Backward compat

`po run` / `po list` / `po show` are untouched. New code lives in a new subcommand and a new module; the existing `_load_formulas` is unchanged (optionally refactor to share entry-point-iteration boilerplate, but only if no behavior change).

## Acceptance criteria (verbatim from issue)

1. New entry-point group `po.deployments`.
2. Example in po-formulas pack: daily epic-sr-8yu nightly run at 09:00.
3. `po deploy` lists all registered, `po deploy --apply` creates them on the Prefect server.
4. README documents the `register()` convention.
5. Existing `po run` still works unchanged.

## Verification strategy

| AC | How verified |
|---|---|
| 1 | Unit test: create a fake distribution with an EP under `po.deployments` (via `importlib.metadata` monkeypatch or a synthetic `EntryPoint`), assert `load_deployments()` returns it. |
| 2 | Integration-ish test: install `po-formulas` editable in the dev venv, run `po deploy` — assert `epic-sr-8yu-nightly` with `Cron("0 9 * * *", ...)` appears. |
| 3 | `po deploy` list: snapshot/assert CLI output contains each registered deployment. `--apply`: unit-test by patching `RunnerDeployment.apply` to a spy and asserting called once per deployment; manual smoke with a local Prefect server (`prefect server start`) then `prefect deployment ls` to see it. |
| 4 | README diff includes a `## Deployments` section with the `register()` example. Grep check in test. |
| 5 | Smoke test: `po list` shows existing formulas; `po run software-dev-full --help`-equivalent (run with `--dry-run` stub backend) exits 0. |

## Test plan

- **Unit** (new `tests/test_deployments.py`):
  - `load_deployments()` iterates EPs, normalizes single vs list return, surfaces loader errors without aborting.
  - `_format_schedule()` helper renders `Cron`/`Interval`/`None` deterministically.
  - `--apply` path calls `.apply()` on each deployment (monkeypatch; no live Prefect).
- **CLI** (Typer `CliRunner`):
  - `po deploy` with no EPs installed → friendly "no deployments" message, exit 0.
  - `po deploy` with one stub EP → listing contains the name.
  - `po run` baseline regression: still loads and invokes a fake formula.
- **E2E / manual** (documented, not CI): `prefect server start` → `PREFECT_API_URL=... po deploy --apply` → `prefect deployment ls` shows the nightly deployment; re-run `--apply` → no duplicates (idempotency).
- **Playwright**: N/A (CLI-only, no UI).

## Risks

- **Prefect 3 API drift**: `RunnerDeployment.apply()` and `prefect.schedules.Cron` are Prefect 3.x; verify exact import paths with context7 before coding (`prefect.deployments` vs `prefect.flows.Flow.to_deployment`). If `Cron` lives under `prefect.client.schemas.schedules`, adjust example.
- **Work pool required for server-visible runs**: a pure `RunnerDeployment.apply()` without `work_pool_name` creates a deployment that has no worker to pick it up. Document this — AC says "creates them on the Prefect server", which `apply()` does, but actual execution needs a work pool/worker. Mention `--work-pool` flag + `prefect worker start -p <pool>` in README.
- **Entry-point loading side effects**: `register()` is called eagerly during `po deploy` even without `--apply`. If a pack does network I/O in `register()`, listing slows. Convention note in README: `register()` should be pure — build objects, no I/O.
- **Idempotency**: `RunnerDeployment.apply()` upserts by `(flow_name, name)`. Verified by re-apply in manual smoke.
- **Cross-repo change**: the example lives in `../../software-dev/po-formulas` (separate path, separate package). We'll edit it in the same branch but that repo may have its own git state — call out in handoff.
- **No migration / API-contract changes** for existing consumers. `po run` untouched.
