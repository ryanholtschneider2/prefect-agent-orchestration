# Plan — prefect-orchestration-1gd

Isolate scratch-loader tests from the live Prefect server by wrapping
the toy `@flow` executions in `prefect.testing.utilities.prefect_test_harness`.

## Affected files

- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_cli_run_from_file.py`
  — add module-scoped autouse `prefect_test_harness` fixture at the top
  of the file.

That is the only file in scope. Verified:

- `tests/test_cli_run_from_file.py` is the only **unit** test file that
  *executes* toy `@flow`s (via `flow_obj()` and via `CliRunner` against
  `cli.app run --from-file`).
- `tests/_fixtures.py::sample_flow` is referenced only by
  `tests/test_deployments.py`, which calls `flow.to_deployment(...)` (a
  pure object constructor — no flow execution, no Prefect API call) and
  uses `monkeypatch.setenv("PREFECT_API_URL", …)` to a dummy host on the
  paths that *do* hit the API. Out of scope.
- `tests/e2e/test_po_run_from_file.py` is in the `e2e` layer, which is
  `PO_SKIP_E2E=1`'d in this rig (per repo `CLAUDE.md`). Not the source
  of the live-UI pollution that the bug describes — that pollution
  comes from the unit suite, which the actor-critic loop runs every
  iteration. Out of scope for this issue (separate cleanup if needed).

No new `tests/conftest.py` will be created. The fix is the smallest
possible blast radius — a single module-local fixture — per the
triage's "narrow the autouse scope to the offending test file"
recommendation.

## Approach

Add at the top of `tests/test_cli_run_from_file.py` (after imports):

```python
@pytest.fixture(scope="module", autouse=True)
def _isolated_prefect_server() -> Iterator[None]:
    """Route all flow executions in this file through an ephemeral
    in-memory Prefect server. Without this, `flow_obj()` and
    `CliRunner` invocations against `po run --from-file` register
    runs against whatever PREFECT_API_URL points at — typically the
    live PO Prefect UI, which gets cluttered with toy `hello`/
    `add`/`beta` runs every PO actor-critic loop.
    """
    from prefect.testing.utilities import prefect_test_harness

    with prefect_test_harness():
        yield
```

Add the matching `from collections.abc import Iterator` import (or
inline a `Generator[None, None, None]` — `Iterator[None]` is cleaner
and already common in the suite).

### Why module-scoped, autouse, file-local

- **Module scope** — sets up the harness once per file run (~hundreds
  of ms for the in-memory SQLite spin-up), amortized across all 14
  tests in the file. Function-scope would multiply that cost ×14.
  Toy flows are stateless (`hello → "hi"`, `add(a,b) → a+b`, …) so
  cross-test state leakage in the harness DB is harmless.
- **Autouse** — every test in this file either constructs or executes
  a toy flow; opt-in via marker would be noise.
- **File-local (no `conftest.py`)** — `tests/test_deployments.py` and
  `tests/test_doctor.py` already drive `PREFECT_API_URL` via
  `monkeypatch.setenv`/`delenv`; a global autouse harness would
  override their setup and may break their assertions. Keep the fix
  surgical. If a second file ever needs the same isolation, hoist to
  `tests/conftest.py` as a non-autouse session-scoped fixture and
  apply via `pytestmark = pytest.mark.usefixtures(...)`.

### Why `prefect_test_harness` is the right tool

`prefect.testing.utilities.prefect_test_harness` (verified at
prefect==3.6.27, signature `(server_startup_timeout: int|None = 30)`)
yields a context manager that:

1. Spins up an ephemeral SQLite-backed Prefect server in-process.
2. Sets `PREFECT_API_URL` and related settings to point at it for the
   duration of the `with` block.
3. Tears it down (DB and settings) on exit.

Because `CliRunner.invoke(cli.app, …)` runs Typer in-process, it sees
the harness's settings via the same Python settings stack — no
subprocess inheritance question. (Reaffirmed by reading the affected
tests: every flow invocation is in-process via either direct call or
`CliRunner`, never a real `subprocess.run`.)

## Acceptance criteria (verbatim)

> Running 'uv run python -m pytest tests/test_cli_run_from_file.py'
> against a live Prefect server produces ZERO new flow runs in the live
> server's UI; all hello/add/beta flow execution stays within the test
> harness's ephemeral DB

## Verification strategy

1. **Pre-check**: with the live PO Prefect server running, capture
   baseline flow-run count:
   ```bash
   prefect flow-run ls --limit 200 | wc -l   # baseline N
   ```
2. **Run the suite**:
   ```bash
   uv run python -m pytest tests/test_cli_run_from_file.py -v
   ```
   All 14 tests must pass.
3. **Post-check**: query the live server for `hello`, `add`, `beta`
   flow runs created in the last few minutes:
   ```bash
   prefect flow-run ls --flow-name hello --flow-name add --flow-name beta --limit 50
   ```
   Must return zero new entries since the pre-check timestamp.
4. **Total-count delta**: re-run `prefect flow-run ls --limit 200 | wc -l`
   — should equal baseline N (same value).
5. **Negative control** (sanity): temporarily comment out the new
   fixture body, rerun step 2 + step 3 — `hello`/`add`/`beta` should
   reappear, confirming the test would have polluted without the fix.
   Restore the fixture before declaring done.

The post-check query is the literal AC: "ZERO new flow runs in the
live server's UI".

## Test plan

- **unit** (`tests/`): the fix lives here. Run
  `uv run python -m pytest tests/test_cli_run_from_file.py -v` to
  confirm the harness doesn't break any of the 14 existing tests
  (especially `test_load_idempotent_for_same_path` which depends on
  module-cache identity, and `test_load_failed_import_does_not_pollute_sys_modules`
  which inspects `sys.modules`). Then run the full
  `uv run python -m pytest` to confirm no neighbor file regresses (the
  harness is module-local so it shouldn't, but verify).
- **e2e** (`tests/e2e/`): skipped in this rig (`PO_SKIP_E2E=1` per
  `.po-env`). Not exercised by the actor-critic loop. No changes here.
- **playwright**: N/A (`has_ui=false`).

## Risks

- **Prefect import path drift.** `prefect_test_harness` lived at
  `prefect.testing.utilities` in Prefect 2.x and stayed there in 3.x;
  verified importable at the current pin (`prefect==3.6.27`). If the
  pin moves, this import is the brittle point.
- **Module-scope state bleed.** The harness's ephemeral DB persists
  across all 14 tests in the file. The toy flows are stateless and
  none of the assertions inspect Prefect server state, so this is
  benign — but worth flagging if someone later adds a test that
  *does* read flow-run history.
- **Settings leak into other test files.** `pytest_test_harness`
  scopes its setting overrides to the `with` block; pytest tears down
  the module-scope fixture before moving to the next file. No leakage
  expected. Will be confirmed by running the full suite (other test
  files that pin `PREFECT_API_URL` via `monkeypatch` should still see
  their patched values).
- **`flow.serve` / `to_deployment` paths.** Not exercised by these
  tests (they only invoke flows directly), so harness coverage is
  sufficient. `tests/test_deployments.py` is unaffected.
- **No API contract change, no migration, no breaking consumers.** The
  fix is test-suite-only.
