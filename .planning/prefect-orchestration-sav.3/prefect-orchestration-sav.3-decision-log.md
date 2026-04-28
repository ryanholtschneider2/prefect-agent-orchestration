# Decision Log: prefect-orchestration-sav.3

- **Decision**: Centralized tmux artifact tracking in a new `tmux_tracker` module rather than threading state through `RoleRegistry` / backend instances.
  **Why**: Backends are per-role and short-lived; the SIGINT handler in `po run` lives in `cli.py` and has no direct handle to all spawned backends. A module-level registry (thread-safe via `threading.Lock`) gives the signal handler one trivial entry point and keeps the layering clean (tracker has no Prefect/typer dependencies).
  **Alternatives considered**: stash a list of refs on `flow_run` context (Prefect ctx is per-task, not per-flow-run); pass a cleanup-callback chain through registries (touches every backend signature).

- **Decision**: `kill_for_issue` scans live tmux state via `tmux list-sessions` / `list-windows` rather than relying on the in-process registry.
  **Why**: `po retry` runs in a *fresh* process — the prior `po run` already crashed and its registry is gone. Scanning is the only way to find leftovers. Registry-based kill_all serves the same-process Ctrl-C path; kill_for_issue serves the cross-process recovery path. They're complementary, not redundant.

- **Decision**: Made `sleep infinity` conditional on `rc != 0` instead of removing it.
  **Why**: Issue's "Also reconsider" suggestion was correct: the keep-alive only matters when claude exits *abnormally* (rate limit, bad arg) and an operator wants `capture-pane` for diagnostics. Clean exits don't need it; the prior unconditional shape was the proximate cause of the 11h-49m zombie observed in the bug report.
  **Alternatives considered**: drop sleep infinity entirely (loses early-exit diagnostics); use `trap` to catch parent-death (more complex, doesn't help the clean-exit case).

- **Decision**: SIGINT handler raises `typer.Exit(128 + signum)` rather than `sys.exit` or letting the default handler run.
  **Why**: Conventional shell exit code for signal-terminated processes (130 for SIGINT, 143 for SIGTERM); typer.Exit gets typer's normal exit-handling, which preserves the runner's exit code in tests.

- **Decision**: Retry's tmux pre-cleanup catches and warns on exceptions (non-fatal) rather than letting them propagate.
  **Why**: Tmux cleanup is best-effort hygiene — if the user doesn't have tmux on PATH or the server is unreachable, retry should still proceed to archive/relaunch. A warn message preserves visibility without blocking the actual work.
