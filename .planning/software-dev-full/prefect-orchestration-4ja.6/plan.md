# Plan: prefect-orchestration-4ja.6 — `po.doctor_checks` entry-point group

## Affected files

Core (`prefect-orchestration` repo):
- `prefect_orchestration/doctor.py` — add `DoctorCheck` public dataclass, entry-point discovery (`_iter_doctor_check_eps`), per-check timeout wrapper, integrate pack checks into `run_doctor`, extend `render_table` with a `SOURCE` column.
- `prefect_orchestration/cli.py` — minor docstring update for `doctor` command (mentions pack contributions).
- `tests/test_doctor.py` — new unit tests for entry-point discovery, timeout-yields-yellow, exit-code-on-red, source column, install-order ordering.
- `CLAUDE.md` — short subsection under "When a task requires writing code here" (or new "Pack contributions" section) documenting the `po.doctor_checks` group.

Sibling pack (`../software-dev/po-formulas/`):
- `po_formulas/checks.py` — new module with at least one example `DoctorCheck`-returning callable (e.g. `claude_cli_authenticated` or `rig_has_planning_dir`).
- `pyproject.toml` — register `[project.entry-points."po.doctor_checks"]`.
- (Optional) `tests/test_checks.py` if the pack has tests; the pack repo currently has none, so add only if trivial.

## Approach

1. **Public dataclass** — add `DoctorCheck` in `doctor.py` with fields `name: str`, `status: Literal["green","yellow","red"]`, `message: str`, `hint: str = ""`. Expose alongside the existing `CheckResult`/`Status` types. Add a thin adapter `_to_check_result(dc: DoctorCheck, source: str) -> CheckResult` mapping `green→OK`, `yellow→WARN`, `red→FAIL`, hint→remediation, and tagging the source pack so the table can show provenance. Keep core `CheckResult` shape stable; add a `source: str = "core"` field to it for the new SOURCE column.

2. **Entry-point discovery** — mirror `_iter_formula_eps()` with `_iter_doctor_check_eps()` returning a list of entry points sorted by their distribution name (install order is unstable; alphabetical-by-pack is deterministic and adequate). Each EP resolves to a zero-arg callable returning `DoctorCheck`.

3. **Timeout wrapper** — wrap each pack check in `concurrent.futures.ThreadPoolExecutor(max_workers=1).submit(fn).result(timeout=PACK_CHECK_TIMEOUT_S)` (default 5s). On `FuturesTimeoutError` produce a yellow `DoctorCheck(name=ep.name, status="yellow", message="check timed out after 5s", hint="...")`. On any other exception produce red. Orphan thread is acceptable per triage. Built-in core checks already have their own timeouts; we don't re-wrap them (avoids regressing existing behavior and keeps changes minimal).

4. **Aggregation order** in `run_doctor`:
   - Run `ALL_CHECKS` (core) first, unchanged.
   - Then iterate sorted pack EPs; for each, load + invoke under timeout, convert to `CheckResult` with `source=ep.dist.name`, append to the same `DoctorReport.results`.
   - Exit-code semantics unchanged: any `FAIL` (i.e. red) → exit 1. AC #4: yellow on timeout. The triage's "all reds → exit 1" decision is honored; no `critical` field added.

5. **Rendering** — extend `render_table` to print a `SOURCE` column (e.g. `core`, `po-formulas-software-dev`). Keep formatting backwards-compatible enough that existing test assertions on the failure/warning summary line still pass.

6. **Pack example check** — implement `claude_cli_authenticated()` in `po_formulas/checks.py`: shells out to `claude --version` (5s subprocess timeout) and returns green/red/yellow accordingly. This dovetails with existing user pain points (Claude CLI auth) and exercises the timeout path naturally. Register via entry point `claude-auth = "po_formulas.checks:claude_cli_authenticated"`.

7. **Docs** — update root `CLAUDE.md`: add a short "Doctor checks" bullet to the "Installed at runtime" / "Common workflows" sections describing the entry-point group, the `DoctorCheck` shape, and that packs ship checks in `po_formulas/checks.py`.

## Acceptance criteria (verbatim)

(1) po.doctor_checks entry-point group defined; (2) 'po doctor' runs core + all pack checks in one table; (3) checks have name/status/message/hint; (4) 5s per-check timeout, yellow on timeout; (5) po-formulas-software-dev ships at least one example check; (6) documented in CLAUDE.md.

## Verification strategy

- **AC1**: unit test asserts `_iter_doctor_check_eps()` returns the registered EP after monkeypatching `entry_points`.
- **AC2**: unit test calls `run_doctor()` with a stub EP iterator yielding one fake check; assert resulting `DoctorReport.results` includes both core and pack rows; assert pack rows have `source != "core"`.
- **AC3**: `DoctorCheck` dataclass fields verified via `dataclasses.fields(...)`; runtime test instantiates one and asserts attributes.
- **AC4**: unit test feeds a `lambda: time.sleep(10)` pack check, monkeypatching the timeout to 0.1s; assert the resulting row has `Status.WARN` and message contains "timed out".
- **AC5**: pack-side: `grep` `po.doctor_checks` in `po-formulas/pyproject.toml`; smoke test imports `po_formulas.checks` and calls the function (returns a `DoctorCheck`).
- **AC6**: grep `CLAUDE.md` for `po.doctor_checks`.
- **End-to-end smoke**: `po doctor` invocation — verify the new column appears, the example pack check runs, and exit code is 0/1 as expected. (Manual; not gated by unit tests since CI lacks a Prefect server.)

## Test plan

- **Unit** (primary): extend `tests/test_doctor.py` with the four AC-driven tests above. Keep tests hermetic (monkeypatch `entry_points`, no real subprocess unless trivially fast).
- **E2E**: not strictly required, but if a `tests/e2e/test_doctor_e2e.py` is cheap, add one that runs `po doctor` as a subprocess against a stubbed env (no Prefect server) and asserts the output table layout. Optional.
- **Pack tests**: pack has no test infra; skip unless trivial.
- **Playwright**: N/A (CLI only).

## Risks

- **Timeout via thread leaves orphaned threads** if a pack check truly hangs. Acceptable for a short-lived CLI; documented in code comment.
- **`DoctorCheck` vs `CheckResult` duplication** — two near-identical types in the public surface. Mitigation: `DoctorCheck` is the *pack-facing* dataclass (matches the issue's design block verbatim), `CheckResult` is the *internal* renderer type. Adapter is one function. Worth the clarity for pack authors.
- **Adding `source` field to `CheckResult`** is a non-breaking additive change (default `"core"`), but downstream consumers that `dataclasses.asdict()` it will see a new key. No known consumers outside the repo.
- **Render-table column addition** could break a test that pins exact table widths; need to update `tests/test_doctor.py` rendering assertions accordingly.
- **Pack `pyproject.toml` change** requires `po update` after install for entry-point metadata to refresh; build will need to run that. Note in build instructions.
- **No migration / API contract risk** — entry-point group is metadata; no DB, no HTTP API.
