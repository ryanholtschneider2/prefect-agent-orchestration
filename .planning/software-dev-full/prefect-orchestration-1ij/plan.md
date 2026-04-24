# Plan — `prefect-orchestration-1ij` — `po doctor`

## Affected files

- `prefect_orchestration/doctor.py` — **new**. Pure-introspection check
  functions + a `run_doctor()` that returns structured results.
- `prefect_orchestration/cli.py` — add `doctor` Typer command that calls
  into `doctor.run_doctor()` and renders the table / exit code.
- `tests/test_doctor.py` — **new**. Unit tests per check function with
  mocks for `subprocess`, `importlib.metadata.entry_points`, and the
  Prefect client; one end-to-end test of `run_doctor()` aggregation +
  exit-code logic.

No changes to `deployments.py` beyond what's already exported.

## Approach

Model on `bd doctor`: a list of independent checks, each returning a
`CheckResult(name, status, message, remediation)`. `status` is one of
`OK | WARN | FAIL`. The CLI prints a fixed-width table with a colored
status column and, for non-OK rows, a remediation hint line beneath.
Exit 0 if no `FAIL`; exit 1 if any `FAIL`. `WARN` never affects exit
code (AC 2).

Checks (critical vs warn per triage):

| # | Name | Class | How |
|---|---|---|---|
| 1 | `bd` on PATH | critical | `shutil.which("bd")`; if present, `subprocess.run(["bd", "--version"], timeout=5)` returns 0 |
| 2 | Prefect API reachable | critical | read `PREFECT_API_URL` env. If unset → FAIL with hint. If set, lazy-import `prefect.client.orchestration.get_client` and `await client.hello()` (or `.api_healthcheck()`), wrapped in `asyncio.run` with a short timeout |
| 3 | At least one work pool | critical | via same client, `await client.read_work_pools()`; empty list → FAIL with `prefect work-pool create po --type process` hint |
| 4 | `po.formulas` entry points load | critical | reuse `cli._load_formulas()` but capture exceptions per-EP; any load failure is FAIL with the offending EP + exception |
| 5 | `po.deployments` register() loads | critical | call `deployments.load_deployments()`; any `LoadError` → FAIL with remediation |
| 6 | `po list` non-empty | critical | `len(_load_formulas()) > 0`; FAIL hint: install a pack (`uv add po-formulas-software-dev`) |
| 7 | uv-tool install fresh | warn | cross-check: set of formula names discovered via `importlib.metadata.entry_points(group="po.formulas")` equals set discovered via fresh `subprocess.run(["po", "list"], ...)` parse. Divergence → WARN "re-run `uv tool install --force …`". If `po` binary not found, skip with WARN. (Chose this over version-comparing editable sources — doesn't require pack paths.) |
| 8 | `LOGFIRE_TOKEN` set | warn | `os.environ.get("LOGFIRE_TOKEN")` truthy; missing → WARN "optional: export `LOGFIRE_TOKEN` to enable telemetry (beads 9cn)" |

Design constraints honored:

- **No side effects**: only GETs (`read_work_pools`, `hello`), `which`,
  `--version`, env reads, EP introspection. No writes, no Prefect
  mutations. (AC 4)
- **Isolation**: each check is wrapped in `try/except Exception` inside
  `run_doctor()` so a blown check surfaces as FAIL without aborting the
  rest. Per-EP isolation inside checks 4/5.
- **Lazy imports**: `prefect.client…` import lives inside the Prefect
  checks so `po --help` stays fast.
- **Pure introspection**: no `.planning/` writes, no server-side
  changes.

Work-pool cross-check against pack-declared pool names (triage open
question): **out of scope for v1**. "At least one pool" matches the AC
wording and the triage's explicit recommendation; leave pool-name
matching as a future enhancement.

Output shape (renders in `cli.doctor`):

```
CHECK                          STATUS   MESSAGE
-----                          ------   -------
bd on PATH                     OK       bd 0.23.1
Prefect API reachable          OK       http://127.0.0.1:4200/api
Work pool exists               FAIL     no work pools registered
  → prefect work-pool create po --type process
Formulas load                  OK       3 formulas
Deployments load               OK       2 deployments
po list non-empty              OK       3 formulas
uv-tool install fresh          OK       entry points match `po list`
LOGFIRE_TOKEN                  WARN     not set (telemetry disabled)

1 failure(s), 1 warning(s).
```

## Acceptance criteria (verbatim from issue)

1. `po doctor` prints a per-check table.
2. Exits 0 when all critical checks pass.
3. Red lines include a remediation hint.
4. Idempotent, no state written.

## Verification strategy

| AC | How checked |
|---|---|
| 1 | Unit test asserts `run_doctor()` returns a list with stable `name` keys for all 8 checks. CLI test via Typer's `CliRunner` asserts output contains header row + one line per check. |
| 2 | Unit test: monkeypatch every check to return `OK`/`WARN` only → `typer.Exit.code == 0`. Mixed with one `FAIL` → code `1`. |
| 3 | Unit test: for every `CheckResult` with status `FAIL`, `.remediation` is non-empty; CLI output for that row must contain the remediation string on the line below. |
| 4 | Unit test: run `run_doctor()` with a tmpdir CWD, assert `os.listdir(tmpdir)` unchanged before/after. Mocked Prefect client asserts only `read_work_pools` / `hello` are called — no `create_*` / `delete_*`. |

Manual smoke (from repo root, before closing bead):

```bash
uv run po doctor                 # with nothing running → expect FAILs on Prefect + pool
prefect server start &           # then:
uv run po doctor                 # Prefect reachable; pool check still FAIL
prefect work-pool create po --type process
uv run po doctor                 # all OK / WARN
```

## Test plan

- **unit** (primary): `tests/test_doctor.py` — one test per check
  function (mocked subprocess / client / EPs); one aggregation test for
  exit-code logic; one CLI test via `typer.testing.CliRunner`.
- **playwright**: N/A — CLI-only, no UI.
- **e2e**: N/A for v1. Live Prefect-server integration is covered by
  manual smoke above; adding a dockerised Prefect fixture is out of
  scope.

## Risks

- **Prefect client API drift**: `client.hello()` vs `api_healthcheck()`
  across Prefect versions. Mitigation: pick whichever is present in the
  pinned Prefect; fall back to a bare `httpx.get(PREFECT_API_URL + "/health")`
  if the import path breaks. No API contract change for PO itself.
- **Subprocess hangs**: `bd --version` or nested `po list` could hang
  on a broken install. All `subprocess.run` calls use `timeout=5` and
  treat `TimeoutExpired` as FAIL.
- **No migrations, no breaking consumers**: new verb, additive only.
  Existing `po list/show/run/deploy` untouched.
- **Baseline failure** (`tests/test_mail.py::test_prompt_fragment_exists_and_mentions_inbox`
  missing `po_formulas/mail_prompt.md`) is unrelated to this issue —
  pre-existing, flagged in baseline, not part of the doctor scope. Do
  not attempt to fix in this bead.
