# Implementation Summary: prefect-orchestration-nfs

## Bug

Two parallel `po run software-dev-full` invocations against the same rig (PO_BACKEND=tmux): one finishes; the other wedges at the first triager step — empty tmux pane, no Claude child, sentinel never fires, parent blocks until `timeout_s` (default 1800 s).

## Fix

Two complementary changes in `prefect_orchestration/agent_session.py`:

1. **Per-rig advisory spawn lock** (`_with_rig_spawn_lock(cwd)`) — fcntl-based exclusive lock at `<cwd>/.planning/.po-claude-spawn.lock`, held only for the brief `_spawn_tmux` + `_wait_for_tui_ready` window. Serializes the Claude-CLI startup race that wedges parallel invocations in the same rig. Released before the multi-minute work turn so per-role concurrency is unaffected. Honors `PO_DISABLE_SPAWN_LOCK=1` for opt-out, degrades to no-op on read-only rigs.

2. **Submission-landed wedge detection** (`_assert_submission_landed`) — after the existing 3× `send-keys Enter` retry loop, if no `active_marker` was observed, give claude one more 30 s grace window then raise `RuntimeError` with the existing `_format_wedge_error` diagnostic + a root-cause hint pointing at the parallel-spawn race and `po retry` workaround. Total worst-case latency: TUI fallback (8 s) + paste retries (~9 s) + grace (30 s) ≈ 47 s, under the 60 s SLA.

3. **`_wait_for_tui_ready` now returns `bool`** — True when the `❯` glyph or `[claude exited` marker rendered, False on fallback-timeout. Caller logs a warning when False; doesn't raise (paste-buffer detection is the firm gate). Existing callers ignore the return value, so backward-compatible.

## Files Changed

- `prefect_orchestration/agent_session.py`: +120 / −10
  - Imports: `contextmanager`, `Iterator`
  - `_wait_for_tui_ready`: signature → `bool`
  - New: `_with_rig_spawn_lock`, `_assert_submission_landed`
  - `TmuxInteractiveClaudeBackend.run`: spawn block now wrapped in lock, captures `tui_ready`, tracks `submission_seen`, calls `_assert_submission_landed` on miss
- `tests/test_agent_session_wedge_nfs.py`: +259 (14 unit tests, all passing)

## Acceptance Criteria

> Two parallel `po run software-dev-full` against same rig both progress past triager 99% of the time, **OR** a clear, actionable error fires within 60 s when wedge detected.

We deliver both:
- Spawn lock targets the 99 % case.
- Detection covers the OR clause; verified via `test_assert_submission_landed_raises_when_pane_stays_empty` and the SLA invariant in `test_total_wedge_latency_under_60s_invariant`.

## Test Results

- **Baseline**: 10 failed, 654 passed, 2 skipped (pre-existing — `test_cli_packs`, `test_deployments`, `test_mail`, `test_session_name_derivation`)
- **Final**:    10 failed, 668 passed, 2 skipped
- **Delta**:    same 10 pre-existing failures, +14 new passes from `test_agent_session_wedge_nfs.py`, 0 regressions
