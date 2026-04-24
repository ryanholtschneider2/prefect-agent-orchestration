# Plan: `prefect-orchestration-4h8.1` — `po artifacts <issue-id>`

## Affected files

- `prefect_orchestration/cli.py` — add `artifacts` Typer subcommand.
- `prefect_orchestration/artifacts.py` — **new module** with the collection/formatting logic (keeps `cli.py` thin, mirrors the `run_lookup` / `status` / `doctor` split).
- `tests/test_cli_artifacts.py` — **new** unit tests (mirror `tests/test_cli_logs.py`).
- Possibly `tests/test_artifacts.py` — unit tests for the pure collection/ordering/rendering helpers.

No changes needed in `run_lookup.py` — `resolve_run_dir()` is already public and that's the bd-metadata helper 5i9 landed.

## Approach

Build a read-only "forensic dump" verb. Implementation:

1. **Resolve run dir** via `run_lookup.resolve_run_dir(issue_id)`. On `RunDirNotFound`, print the hint to stderr and exit 2 (same UX as `po logs`). The triage note about "fallback to searching common rig-path locations" is out of scope — `resolve_run_dir` already emits a repair hint, and principles §1 says don't build redundant fallbacks. Triage flagged this only as an open question; resolving it with "reuse the existing helper" is consistent with the rest of the CLI.

2. **Collect files** in `artifacts.py` into an ordered section list:
   - `triage.md`
   - `plan.md`
   - Interleaved `critique-iter-N.md` + `verification-report-iter-N.md` sorted by integer `N` (same N grouped together; critique before verification for that N). Parse `N` with a regex (`re.search(r"iter-(\d+)\.md$")`) so `iter-10` sorts after `iter-2`.
   - `decision-log.md`
   - `lessons-learned.md`
   - `verdicts/*.json` sorted alphabetically by filename.
   - Missing files emit a section header + `(missing)` line — never abort.

3. **Rendering** — for each section: `===== <relative-path> =====\n<body>\n` (matches the header style `po logs` uses so `less` scrollback feels familiar). JSON verdicts are pretty-printed (`json.dumps(data, indent=2, sort_keys=True)`) with a graceful fallback to raw text if parse fails.

4. **Flags**:
   - `--verdicts` — collect only `verdicts/*.json` sections, skip everything else.
   - `--open` — short-circuit printing; `subprocess.run([editor, str(run_dir)])` where `editor = os.environ.get("EDITOR") or shutil.which("xdg-open") or "xdg-open"`. Exit 0 on success, 5 on neither available.
   - `--verdicts` and `--open` are mutually exclusive; the latter wins if both passed (document in help).

5. **Piping hygiene** — use `typer.echo` (no colour) and `sys.stdout.isatty()` check is not needed since nothing in the output is coloured. AC4 ("clean output when piped to less") is satisfied by plain ASCII + trailing newlines.

## Acceptance criteria (verbatim)

> (1) prints triage + plan + every critique/verification iter in order; (2) --verdicts flag prints just the JSON verdict files; (3) --open launches EDITOR on the dir; (4) clean output when piped to less.

## Verification strategy

| AC  | How checked |
|-----|-------------|
| (1) | Unit test: seed a run dir with `triage.md`, `plan.md`, `critique-iter-1.md`, `verification-report-iter-1.md`, `critique-iter-2.md`, `verification-report-iter-2.md`, `critique-iter-10.md`, `verification-report-iter-10.md`, `decision-log.md`, `lessons-learned.md`. Invoke `po artifacts beads-xyz`, assert each filename header appears in `result.stdout` in the expected order (assert `stdout.find("triage.md") < stdout.find("plan.md") < stdout.find("iter-1.md") < stdout.find("iter-2.md") < stdout.find("iter-10.md") < stdout.find("decision-log.md") < stdout.find("lessons-learned.md")`). Confirms iter ordering is numeric. |
| (2) | Unit test: seed run dir with mixed files plus `verdicts/triage.json`, `verdicts/build-iter-1.json`. Invoke with `--verdicts`; assert `triage.json` and `build-iter-1.json` headers appear, and `triage.md` / `plan.md` do not. |
| (3) | Unit test: monkeypatch `subprocess.run` to capture argv. Invoke with `--open`; assert the captured command is `[<EDITOR>, <run_dir>]` and exit code is 0. Test both `EDITOR=vim` (env set) and fallback-to-`xdg-open` (env unset, `shutil.which` patched). |
| (4) | Unit test: assert no ANSI escape bytes (`"\x1b["`) in stdout. Manual sanity: `po artifacts <id> | less` during build review. |

Additional test: `RunDirNotFound` path — exit 2, stderr contains the repair hint (mirrors `test_cli_logs.py::test_logs_missing_metadata_shows_fix_hint`).

## Test plan

- **Unit** (`tests/test_cli_artifacts.py`, maybe `tests/test_artifacts.py`): exercises the CliRunner surface and the pure collection/ordering helpers. Pattern copied from `test_cli_logs.py` — monkeypatch `run_lookup.resolve_run_dir`, seed `tmp_path` with fixture files.
- **Playwright**: N/A (CLI only).
- **e2e**: not strictly required; `tests/e2e/` houses subprocess-level CLI smoke tests (`test_po_status_cli.py` pattern). Worth one small e2e that shells out to `po artifacts --help` to confirm entry-point registration. Defer if unit coverage is solid.

## Risks

- **None for migrations / DB** — read-only command.
- **API contract** — new verb only; no existing-command signatures change.
- **Consumer breakage** — none.
- **Minor** — `--open`'s `subprocess.run` on an `EDITOR=vim` invocation will block stdin on interactive editors; acceptable (user explicitly asked to open the dir).
- **Minor** — `xdg-open` on a directory on Linux desktops opens a file manager; on bare servers it may error. Catch non-zero return and echo a hint.
- **Iter regex assumption** — filename pattern is `critique-iter-N.md` / `verification-report-iter-N.md`. If a pack uses a different scheme, those files fall through to "unrecognised" and won't print. Not a regression (no current pack deviates); document the glob in the module docstring so future packs know.
