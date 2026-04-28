# Decision Log: prefect-orchestration-nfs

## Decisions

- **Decision**: Use `fcntl.flock` (file-based lock) instead of `threading.Lock` for per-rig spawn serialization.
  **Why**: Two parallel `po run` invocations are independent OS processes — a Python in-memory lock cannot serialize across processes. The wedge happens specifically because of cross-process contention.
  **Alternatives considered**: `multiprocessing.Lock` (heavier, requires shared memory setup); `os.O_EXCL` lockfile (fragile cleanup on crash). `fcntl.flock` is auto-released by the OS on FD close and survives ungraceful exits — same pattern already used in `_ensure_stop_hook` (4a80e0e fix).

- **Decision**: Hold the spawn lock only for `_spawn_tmux` + `_wait_for_tui_ready` (the brief startup window), not the full work turn.
  **Why**: Holding it for the work turn would serialize all tmux-backend agents in a rig — collapsing concurrency the system relies on (`lint ∥ unit ∥ e2e ∥ playwright` fan-out). The race is a startup-time issue (Claude CLI's on-disk state mkdir/credentials/OAuth refresh), not a steady-state one.

- **Decision**: Grace window for `_assert_submission_landed` defaults to 30 s.
  **Why**: Total wedge-detection latency budget is 60 s (acceptance criterion). TUI fallback (8 s) + 3× paste retries (~9 s) + grace (30 s) = ~47 s, leaving headroom for the orchestrator to surface the error. 30 s tolerates a slow but normal claude startup tail without false-positive on healthy runs.

- **Decision**: Log-only (warning) when `_wait_for_tui_ready` returns False, instead of raising immediately.
  **Why**: The `❯` glyph detection is heuristic — terminal-width quirks, Unicode rendering differences, or splash-screen timing variance can produce false negatives. The downstream `_assert_submission_landed` is the firm gate (it actually checks for *active* markers, not just rendering). Logging gives operators a correlation breadcrumb without false-failing healthy runs.

- **Decision**: `PO_DISABLE_SPAWN_LOCK=1` env-var opt-out.
  **Why**: A future Claude CLI release may fix the underlying race, at which point the lock is just latency overhead. Env-gating lets us disable without a code change. Also useful for diagnostic A/B testing — confirm a wedge still reproduces without the lock.

- **Decision**: `_with_rig_spawn_lock` degrades to no-op when `<cwd>/.planning/` mkdir fails (e.g. read-only rig).
  **Why**: Tanking the run because we can't lay down a lockfile is worse UX than skipping the lock — the wedge-detection path still catches the failure mode if it occurs. Keeps the fix non-fatal in unusual deployments.

- **Decision**: Forward-reference `_format_wedge_error` from `_assert_submission_landed` (defined later in file).
  **Why**: Python resolves names at call time, not definition time, so this works without restructuring. Putting `_assert_submission_landed` near `_wait_for_tui_ready` (the only caller's neighborhood) is more cohesive than re-ordering the file by definition order.
