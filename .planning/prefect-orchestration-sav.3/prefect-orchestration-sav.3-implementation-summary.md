# Implementation Summary: prefect-orchestration-sav.3

## Files

### New
- `prefect_orchestration/tmux_tracker.py` — Thread-safe in-process registry of `(session_name, window_name, target)` plus cross-process scanners `kill_all()` (drains registry on signal) and `kill_for_issue(issue_id)` (scans `tmux list-sessions` / `list-windows`, kills matching `po-{safe_issue}-*` sessions and `{safe_issue}-*` windows in shared `po-{rig}` sessions).
- `tests/test_tmux_tracker.py` — 9 unit tests covering register/snapshot/unregister, kill-kind dispatch, no-tmux noop, scoped + unscoped + dot-sanitized scans.
- `tests/test_cli_run_signal.py` — 2 tests: handler install/restore around `flow_obj(...)` and that the installed handler invokes `kill_all()` and exits with `128 + signum`.

### Modified
- `prefect_orchestration/agent_session.py`
  - `_spawn_tmux` registers a `TmuxRef` after both scoped (`@<wid>`) and unscoped (session-name) spawns.
  - `_cleanup_tmux` calls `tmux_tracker.unregister_by_target(target)`.
  - `TmuxInteractiveClaudeBackend.run` wrapper changed from `<cmd> ; sleep infinity` to `<cmd>; rc=$?; if [ "$rc" -ne 0 ]; then echo "..."; sleep infinity; fi` so clean exits collapse the pane (sav.3 root cause).
- `prefect_orchestration/retry.py` — Inside the advisory lock, before archiving, calls `tmux_tracker.kill_for_issue(issue_id)`. Wrapped in try/except → warn (non-fatal).
- `prefect_orchestration/cli.py` `run()` — Installs SIGINT/SIGTERM handlers around `flow_obj(**kwargs)` that call `tmux_tracker.kill_all()` and raise `typer.Exit(128 + signum)`. Prior handlers restored in `finally`.
- `tests/test_retry.py` — 2 new tests: `test_retry_kills_prior_tmux_for_issue`, `test_retry_tmux_cleanup_failure_is_nonfatal`.
- `tests/test_agent_session_tmux.py` — 1 new test inspecting the wrapper source for the rc != 0 guard (regression for the sav.3 root cause).

## Behavior changes
1. `po retry <id>` now kills any leftover tmux session/window for the issue before archiving the run dir.
2. Ctrl-C (or SIGTERM) on `po run` drains the spawned-tmux registry before exiting with conventional 128 + signum.
3. `TmuxInteractiveClaudeBackend` no longer leaks zombie panes on clean claude exits — `sleep infinity` only fires on abnormal exit.
4. `_spawn_tmux` / `_cleanup_tmux` keep the in-process registry in sync (transparent to backend callers).

## Out of scope (deliberately deferred)
- A startup-time sweeper for orphan sessions across reboots — the issue framed this as "consider"; the three landed mechanisms cover the common failure modes.
- Killing the whole tmux server on signal — too aggressive; would clobber unrelated user sessions.
