# Decision log — prefect-orchestration-shj (build iter 1)

- **Decision**: `register()` returns `RunnerDeployment` objects (from `flow.to_deployment(...)`), not raw `flow.serve` / `flow.deploy` calls.
  **Why**: Plan §Convention. `RunnerDeployment.apply()` is the Prefect 3 server-upsert primitive; lets core call `.apply()` uniformly for `--apply` and avoids the pack committing to `serve` vs `deploy` at declaration time.
  **Alternatives considered**: passing raw `(flow, schedule, ...)` tuples (more boilerplate in core); requiring `register()` to call `.serve()` itself (blocks the CLI — wrong for listing).

- **Decision**: Normalize `register()` return values: accept single deployment, list, tuple, or `None`.
  **Why**: Plan §Discovery. Keeps the pack API ergonomic (`return my_dep` works) while core normalizes to a flat list.
  **Alternatives considered**: strict `list[...]`-only contract — rejected as unnecessarily rigid.

- **Decision**: Entry-point loading errors and `register()` exceptions are collected into a `LoadError` list instead of raised.
  **Why**: Plan §Discovery — "one bad pack doesn't mask others". The CLI surfaces warnings to stderr, still exits non-zero when there were errors.
  **Alternatives considered**: fail-fast on first error; silent swallowing (neither matches the plan).

- **Decision**: `--apply` refuses to run when `PREFECT_API_URL` is unset, exiting 2.
  **Why**: Plan §`--apply`. Without an API URL Prefect falls back to an ephemeral SQLite DB, which is almost never the user's intent when they explicitly ask to apply to "the Prefect server".
  **Alternatives considered**: let Prefect pick the default (silent wrong-target footgun); require `--api-url` flag (redundant with Prefect's own env var).

- **Decision**: `format_schedule` inspects `RunnerDeployment.schedules` (Prefect 3, list of `DeploymentScheduleCreate`) with a fallback to `.schedule` (Prefect 2 shape).
  **Why**: Confirmed Prefect 3 shape by live inspection — `d.schedules[0].schedule` is a `CronSchedule`/`IntervalSchedule`. `.schedule` fallback keeps things robust if Prefect refactors.
  **Alternatives considered**: rely on `repr(schedule)` (ugly, not stable); import Prefect schedule classes for isinstance (adds import churn — we use class-name strings instead).

- **Decision**: Unit tests stub entry-point discovery via `monkeypatch.setattr(deployments_mod, "_iter_entry_points", ...)`.
  **Why**: Testing real `importlib.metadata` by installing fake distributions is heavy; the internal helper is the right seam — cheap to stub, keeps tests fast.
  **Alternatives considered**: `importlib.metadata` monkeypatch (more fragile across Python versions); writing `.dist-info` to a tmp path (slow).

- **Decision**: `sample_flow` fixture lives in `tests/_fixtures.py` (a module file), not inline in the test.
  **Why**: Prefect 3's `flow.to_deployment` raises when the flow is defined interactively or in `__main__`. It needs an importable module with a file location.
  **Alternatives considered**: mocking the deployment entirely — doesn't exercise the real schedule-formatting path.

## Revision round (verifier feedback — iter 1)

- **Decision**: Installed `po-formulas-software-dev` editable into the rig's venv so the `po.deployments` entry-point group is actually registered.
  **Why**: Verifier's smoke showed "no deployments registered" because adding a new entry-point group to pyproject.toml is a metadata change that the editable install didn't pick up until re-run (EP metadata is written at install time, not on code reload).
  **Alternatives considered**: dropping a sitecustomize shim (too invasive); inline-registering a fallback EP in core (violates plan's "core is flow-agnostic"); asking the verifier to run `uv sync` (shouldn't be necessary — but is the convention used by the rig's CLAUDE.md dev-install instructions).

- **Decision**: Integration test `test_po_formulas_pack_exposes_epic_sr_8yu_nightly` shells out to `.venv/bin/po deploy` (subprocess) rather than calling `load_deployments()` in-process.
  **Why**: This repo contains a sibling `po_formulas/` directory (from issue prefect-orchestration-5kj — `mail.py` helper) that shadows the editable po-formulas-software-dev install when pytest adds the repo root to `sys.path`. The console script doesn't have cwd on sys.path, so it resolves correctly against the editable install — which is exactly the path the verifier's smoke uses. Running through the script validates the same user-visible behavior.
  **Alternatives considered**: deleting/renaming the local `po_formulas/__init__.py` (would break `tests/test_mail.py` from the sibling issue); adding a pytest path-manipulation fixture (brittle, hides the real sys.path conflict); using `importlib.metadata` stubbing (wouldn't exercise the real EP load path). The subprocess approach also hardens against any future python-path surprises since it exercises exactly what a user would type.

- **Decision**: Integration test skips (not fails) when the pack isn't installed.
  **Why**: AC2 is a "pack ships example" requirement — if the pack isn't installed in the env at all (e.g. a minimal CI container that only has core), the test has nothing to assert. Skip is correct; the unit tests plus this skip-or-pass gate cover both dev-rig and CI-minimal shapes.
  **Alternatives considered**: hard-failing the test when the pack is absent — would block CI for unrelated repos importing core.
