# Plan — `prefect-orchestration-4h8.4`

`po watch <issue-id>` — live merged feed of Prefect flow-state transitions
+ run_dir artifact appearances/changes, in one terminal, with `--replay`
backfill and clean Ctrl-C.

The bulk of this feature already exists from a prior build iteration
(`prefect_orchestration/watch.py`, the Typer `watch` command in
`cli.py`, unit tests in `tests/test_watch.py`, e2e tests in
`tests/e2e/test_po_watch_cli.py`). One unit test is currently failing
(`test_run_watch_emits_live_run_dir_events`), which means the
live-run-dir producer either races the test driver or never fires for
new files. This iteration finishes that polish + makes the failing
tests green; it does not redesign the architecture.

## Affected files

- `prefect_orchestration/watch.py` — fix `_poll_run_dir` so newly-created
  files are emitted within one poll tick (likely a sentinel/race issue
  in the snapshot-then-loop bootstrap, or `RUN_DIR_POLL_S` is too coarse
  for the test's `run_for=0.08`).
- `tests/test_watch.py` — if the test relies on the producer firing in
  <100 ms, lower the run_dir poll interval via the existing
  `poll_run_dir_s=` kwarg in the test helper rather than baking a smaller
  default into the module.
- `prefect_orchestration/cli.py` — verify the `watch` command's
  `--replay` / `--replay-n` plumbing matches the helper signature; no
  expected changes unless wiring drifted.
- `CLAUDE.md` — already documents `po watch`; verify it stays accurate.
- `.planning/software-dev-full/prefect-orchestration-4h8.4/decision-log.md`
  — append the race-fix rationale.

## Approach

1. **Reproduce the failure**: run
   `uv run python -m pytest tests/test_watch.py::test_run_watch_emits_live_run_dir_events -x -q`
   and inspect why `new.md` isn't seen. Two likely causes:
   - The driver calls `wait_for(..., timeout=0.08)` which expires before
     the run_dir poll tick (default 1.0 s) ever runs. Fix in the test by
     passing `poll_run_dir_s=0.01`, or in the helper by exposing a
     "tick now" hook for tests.
   - The initial snapshot includes the file the test creates *after*
     `run_watch` starts but *before* the first tick, then the diff sees
     no change. Fix by ensuring the seed snapshot is taken inside the
     producer task (not synchronously before await).
2. **Fix the underlying race**, not just the test. Prefer the
   producer-side seed: snapshot files inside `_poll_run_dir` on first
   iteration, then sleep, then diff. That way a file created after
   `run_watch` is awaited but before the first sleep elapses still
   appears as `new`.
3. **Re-run the full suite** — green.
4. **Append decision-log entry** describing the race + fix.
5. **Persist** `git diff > build-iter-N.diff` and commit with scoped
   `git add <path>` per parallel-run hygiene.

No new dependencies. `watchdog` remains optional; pure-polling stays the
default path.

## Acceptance criteria (verbatim from issue)

(1) live stream of both sources in one terminal; (2) Ctrl-C exits
cleanly; (3) `--replay` prints existing artifacts + last N state
transitions before following; (4) gracefully degrades if only one
source is available (e.g., run finished so no more state changes —
still show new artifacts); (5) no extra deps beyond `prefect_client` +
`watchdog` (optional).

## Verification strategy

| AC | How verified |
|---|---|
| 1 | `tests/test_watch.py::test_run_watch_emits_live_run_dir_events` (post-fix) + the merged-events unit test prove both producers feed one queue/consumer. |
| 2 | `tests/test_watch.py::test_run_watch_cancels_cleanly` exercises producer cancellation; CLI handler raises `typer.Exit(0)` on `KeyboardInterrupt`. |
| 3 | `tests/test_watch.py::test_build_run_dir_replay_*` and `test_build_prefect_replay_*` plus the `--replay` e2e in `tests/e2e/test_po_watch_cli.py` (which asserts the `===== live =====` separator and pre-separator artifact lines). |
| 4 | `test_run_watch_terminal_on_start` (Prefect side terminal → still emits run_dir events) and `test_run_watch_no_flow_run_for_issue` (Prefect lookup empty → run_dir watcher still streams). |
| 5 | `pyproject.toml` diff: no new runtime deps. `watchdog` stays import-guarded inside `watch.py`. |

## Test plan

- **Unit** (`tests/test_watch.py`): all 18 cases must pass; the
  currently-red `test_run_watch_emits_live_run_dir_events` is the
  immediate target.
- **E2E** (`tests/e2e/test_po_watch_cli.py`): subprocess `po watch`
  smoke + monkeypatched `get_client` for `--replay` separator + missing
  metadata exit-2.
- **Full suite**: `uv run python -m pytest -q` to confirm no regressions
  (other failing tests in baseline — `test_po_deploy_cli`,
  `test_agent_session_tmux`, `test_deployments`, `test_mail` — are
  pre-existing and out-of-scope for this issue; verify they don't
  degrade further).

## Risks

- **Async race tweaks** (snapshot-on-first-tick) could change replay
  semantics — mitigate by keeping `build_run_dir_replay` as a separate
  pure helper that callers (CLI + test) invoke explicitly before the
  producer starts.
- **No API/contract changes**: `po watch` is a new verb; nothing
  external consumes its output format. Safe to refine the prefix /
  separator strings.
- **No migrations.** Pure read-side feature.
- **Out-of-scope failing tests** in baseline (mail prompt md, deploy
  CLI without API URL, tmux argv, deployments listing) belong to other
  beads and must not be silently "fixed" here — flag in decision log
  if encountered.
