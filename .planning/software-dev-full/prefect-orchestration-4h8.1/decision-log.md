# Decision log — prefect-orchestration-4h8.1

- **Decision**: Split pure collection/rendering into `prefect_orchestration/artifacts.py`; keep the Typer command thin in `cli.py`.
  **Why**: Mirrors the existing `run_lookup` / `status` / `doctor` split (plan §Affected files), keeps unit tests easy.
  **Alternatives considered**: Inline everything in `cli.py` (rejected — worse testability, diverges from codebase pattern).

- **Decision**: Reused `run_lookup.resolve_run_dir()` directly; did not add a fallback search over `<rig>/.planning/<formula>/<issue>/`.
  **Why**: Plan §Approach.1 — `resolve_run_dir` already emits a repair hint; principles §1 warns against redundant fallbacks. Triage listed this as an open question, not a requirement.
  **Alternatives considered**: Glob-based fallback keyed off rig path. Rejected to keep behaviour identical across sibling verbs (`po logs`, `po artifacts`, future `po watch`).

- **Decision**: `--open` uses `os.environ["EDITOR"]` first, `shutil.which("xdg-open")` as fallback; both missing → exit 5.
  **Why**: Plan §Approach.4. TTY users typically set `$EDITOR`; desktop users have `xdg-open`; bare servers get a clear error.
  **Alternatives considered**: Hardcoding `xdg-open` (rejected — ignores `$EDITOR`), using `click.launch` (rejected — opens the dir in the default handler which is usually less useful for a `.planning/` dir).

- **Decision**: `--open` takes precedence over `--verdicts` when both are passed.
  **Why**: Plan §Approach.4 documents the precedence; `--open` is a terminal action (launch and return) so printing is moot.
  **Alternatives considered**: Raising `BadParameter` for mutual exclusion — rejected as unfriendly.

- **Decision**: Missing files render as a section with body `(missing)` rather than being skipped.
  **Why**: Plan §Approach.2 + §Verification strategy — makes it obvious to the user which expected artifacts never got written, matches AC1's "every critique/verification iter" even for partial runs.
  **Alternatives considered**: Silent skip (rejected — hides which steps failed to produce output).

- **Decision**: Left the concurrently-added `from prefect_orchestration import sessions as _sessions` import in `cli.py` untouched.
  **Why**: Parallel-run hygiene — another worker (4h8.2 or 4h8.3) added `prefect_orchestration/sessions.py` and the matching import. Not my work; don't revert.
  **Alternatives considered**: None — instructions are explicit about leaving other workers' in-flight changes alone.
