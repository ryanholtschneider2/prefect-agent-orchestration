# Plan: prefect-orchestration-5i9 — `po logs <issue-id>`

## Affected files

- `prefect_orchestration/cli.py` — add `logs` Typer subcommand.
- `prefect_orchestration/run_lookup.py` — **new**: shared helper
  `resolve_run_dir(issue_id) -> (rig_path, run_dir)` reading bead metadata
  via `BeadsStore`. Used by `po logs`, and reusable by the blocked verbs
  (`8bd`, `cdu`, `qrv`, `zrk`).
- `prefect_orchestration/beads_meta.py` — fix `BeadsStore.set` to use the
  correct CLI flag `--set-metadata key=value` (current code uses
  `--metadata` which expects a JSON string; help output confirms
  `--set-metadata` stringArray is the repeatable key=value form). This
  is required for the metadata write in the flow to work.
- `../software-dev/po-formulas/po_formulas/software_dev.py` — at flow
  entry (right after `run_dir` is created and before `triage`), persist
  `po.rig_path` and `po.run_dir` on the bead via the existing
  `BeadsStore` if `parent_bead` is set; if not, write to
  `BeadsStore(parent_id=issue_id)` directly (the issue itself). No-op
  gracefully if `bd` is absent.
- `tests/test_run_lookup.py` — **new**: unit tests for the resolver
  (happy path via monkey-patched `subprocess.run`, missing-metadata
  error, run-dir-gone-from-disk error).
- `tests/test_cli_logs.py` — **new**: unit tests for `po logs` picking
  the newest file, tailing N lines, and the missing-metadata fix-hint.

## Approach

1. **Flow-entry metadata write (pack).** In `software_dev_full`, after
   `run_dir.mkdir(...)`, call a tiny helper that shells out to
   `bd update <issue> --set-metadata po.rig_path=<abs> --set-metadata po.run_dir=<abs>`
   (best-effort, `check=False`, skipped when `bd` is not on PATH or
   `dry_run=True`). Overwrites on every run — acceptable because
   concurrent `po run` on the same issue is already racy at the
   `$RUN_DIR` level; `qrv` (retry) will be the one to introduce a fresh
   per-run sub-dir later.

2. **Shared lookup helper (core).** `run_lookup.resolve_run_dir(issue)`
   reads `bd show <issue> --json`, pulls `metadata["po.rig_path"]` and
   `metadata["po.run_dir"]`, returns `(Path, Path)`. Raises
   `RunDirNotFound` with a message like:

   > no run_dir recorded for <issue>. Has `po run software-dev-full
   > --issue-id <issue> …` been executed? If the flow ran before this
   > infra change, rerun it, or set manually with:
   >   bd update <issue> --set-metadata po.rig_path=<abs> --set-metadata po.run_dir=<abs>

3. **`po logs` subcommand.**
   - Signature: `po logs <issue> [-n N] [-f] [--file NAME]`.
   - Calls `resolve_run_dir`.
   - Builds a candidate list, in priority order:
     1. Prefect flow log: `/tmp/prefect-orchestration-runs/*.log` whose
        mtime falls within the run_dir lifetime (best-effort glob; skip
        if none).
     2. `<run_dir>/lint-iter-*.log`, `test-iter-*.log`, `e2e-iter-*.log`
        (if present).
     3. `<run_dir>/decision-log.md`.
   - "Freshest" = max mtime across the candidate set. Deterministic tie
     break: alphabetical by path.
   - Default action: print the last `n` lines (default 200) with a
     `===== <relative-path> =====` header.
   - `--file NAME`: override selection with exact filename relative to
     run_dir.
   - `-f/--follow`: `os.execvp("tail", ["tail", "-n", str(n), "-F", path])`
     — subprocess exec keeps Ctrl-C clean and avoids reimplementing
     tail. POSIX-only, acceptable (Prefect itself requires POSIX).
   - No new deps.

4. **Error UX.** Missing `bd`, missing metadata, or empty run_dir each
   emit a distinct single-line error to stderr and exit non-zero. The
   metadata-missing case carries the `bd update --set-metadata` hint
   verbatim.

## Acceptance criteria (verbatim from issue)

1. Flow entry sets bead metadata `po.rig_path` and `po.run_dir`.
2. `po logs <issue>` prints tail of the newest log file.
3. `-f/--follow` streams new lines.
4. Missing metadata → error with fix hint.
5. No new deps.

## Verification strategy

| AC | How verified |
|----|--------------|
| 1  | `po run software-dev-full --dry-run --issue-id <id> --rig t --rig-path <tmp>` then `bd show <id> --json \| jq .metadata` shows both keys populated with absolute paths. Also covered by a pack-level assertion after flow entry in an e2e test. |
| 2  | Unit test: seed a fake run_dir with files of known mtimes; invoke `logs(issue_id)` with monkey-patched `resolve_run_dir`; assert stdout contains the newest file's last N lines and the expected header. |
| 3  | Unit test: monkey-patch `os.execvp` and assert it is called with `["tail", "-n", "200", "-F", <path>]`. (Real streaming is out of scope for unit tests; exec is the observable.) |
| 4  | Unit test: `resolve_run_dir` raises when `bd show --json` returns empty metadata; CLI catches it, emits message containing `"bd update"` and `"--set-metadata po.rig_path="`, exits non-zero. |
| 5  | `git diff pyproject.toml` shows no new runtime deps; `uv tool install --force …` still resolves with the existing lockfile. |

## Test plan

- **Unit** (`tests/test_run_lookup.py`, `tests/test_cli_logs.py`):
  resolver happy/error paths; CLI file selection, tail header, fix-hint
  message, `--follow` exec argv.
- **e2e** (`tests/e2e/test_po_logs.py`, new): spawn a throwaway bead via
  `bd create`, shell `bd update --set-metadata` directly (stand in for
  the pack side), create a temp run_dir with a `decision-log.md`, run
  `po logs <id>` as a subprocess, assert the file content appears.
  Skip when `bd` not on PATH. (No real Prefect run — we don't want e2e
  tests spawning Claude.)
- **Playwright**: N/A (CLI-only, `has_ui=false`).

Baseline has one pre-existing failure (`test_prompt_fragment_exists_and_mentions_inbox`)
unrelated to this change — do not treat as a regression.

## Risks

- **Cross-repo change.** Pack and core must be reinstalled together
  (`uv tool install --force --editable … --with-editable …`). Document
  in the PR body / decision-log.
- **BeadsStore.set flag fix is a subtle bug-fix along for the ride.**
  It was almost certainly silently failing before (passing
  `--metadata po.rig_path=...` would be parsed by beads as a JSON
  string, which would error or write nothing). Scope stays narrow:
  only change the flag, keep the signature.
- **Log-path heuristic drift.** If the pack later renames iter log
  files, the candidate globs get stale. Mitigated by `--file` override
  and by keeping the glob list in one place (`run_lookup.py`) so the
  blocked verbs reuse the same definition.
- **Concurrent runs on same issue** overwrite metadata; called out in
  the issue's DESIGN and deferred to `qrv`.
- **No API contract change**; no breaking consumers.
