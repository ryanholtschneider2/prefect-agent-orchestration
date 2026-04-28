# Plan: prefect-orchestration-sav.3 — clean up zombie claude+tmux on po flow death

## Problem
- Wrapper script in `TmuxInteractiveClaudeBackend` (agent_session.py:1008-1013) ends with unconditional `sleep infinity`, so even on clean claude exit the bash wrapper persists.
- `po retry` archives run_dir but leaves prior tmux session running.
- `po run` has no SIGINT/SIGTERM handler — Ctrl-C exits the parent but tmux sessions (detached) keep running with their claude children indefinitely.

## Approach

### 1. New module `prefect_orchestration/tmux_tracker.py`
Module-level thread-safe registry of live tmux references spawned during this process plus two cross-process scanners.

API:
- `TmuxRef(session_name, window_name, target)` — frozen dataclass
- `register(ref)` / `unregister_by_target(target)` — in-process bookkeeping called from `_spawn_tmux` / `_cleanup_tmux`
- `kill_all() -> int` — drain in-process registry; for SIGINT handler
- `kill_for_issue(issue_id) -> int` — scan `tmux list-sessions` + `tmux list-windows` for `po-{safe_issue}-*` (unscoped) and `{safe_issue}-*` windows under `po-*` shared sessions (scoped); used by `po retry` since prior process already exited

### 2. `agent_session.py` edits
- After `_spawn_tmux` returns target, register `(session_name, window_name, target)`.
- `_cleanup_tmux` unregisters.
- Make `TmuxInteractiveClaudeBackend` wrapper's `sleep infinity` conditional on non-zero claude exit:
  ```bash
  cd … && <argv>; rc=$?; if [ "$rc" -ne 0 ]; then echo "[claude exited $rc — diagnostics]"; sleep infinity; fi
  ```
  Clean-exit case: bash wrapper exits → tmux pane collapses → no zombie.

### 3. `retry.py` edits
Before `_archive_run_dir`, call `tmux_tracker.kill_for_issue(issue_id)`. Best-effort, no-op if tmux missing or no matches.

### 4. `cli.py` `run()` edits
Install SIGINT + SIGTERM handlers around `flow_obj(**kwargs)` that:
- Call `tmux_tracker.kill_all()`
- Echo count
- Restore prior handlers in `finally`

Use `signal.signal` only on the main thread; Prefect runs the flow synchronously in-process so the parent owns the signal.

## Verification Strategy

| Criterion | Method | Concrete check |
|---|---|---|
| 1. After `po retry <id>`, prior tmux session for that issue is killed | Unit test | Patch `tmux_tracker.kill_for_issue`; assert called with issue_id once `retry_issue` runs |
| 2. SIGINT to `po run` cleans up tmux sessions before exiting | Unit test | Invoke `run()` with a flow that registers a TmuxRef then raises KeyboardInterrupt; assert `kill_all` ran |
| 3. No leftover tmux session/PID for issue after hung-flow + retry + completion | Smoke (manual + tracker logic) | tracker `kill_for_issue` correctly matches `po-{safe_issue}-{role}` session names |
| 4. Sleep infinity only fires on abnormal claude exit | Unit test on wrapper string | Build wrapper, assert `if [ "$rc" -ne 0 ]` shape present and `sleep infinity` is inside it |

## Files touched
- `prefect_orchestration/tmux_tracker.py` (new)
- `prefect_orchestration/agent_session.py` (register/unregister + wrapper conditional)
- `prefect_orchestration/retry.py` (kill_for_issue call)
- `prefect_orchestration/cli.py` (signal handler in `run()`)
- `tests/test_tmux_tracker.py` (new)
- `tests/test_agent_session_tmux.py` (assertion on new wrapper shape)
- `tests/test_retry.py` (kill_for_issue called)
- `tests/test_cli_run_signal.py` (new)

## Non-goals
- Sweeper for orphan sessions across reboots — issue says "consider"; out of scope.
- Killing whole tmux server — too aggressive.
