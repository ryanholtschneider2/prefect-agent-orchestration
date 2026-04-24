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
