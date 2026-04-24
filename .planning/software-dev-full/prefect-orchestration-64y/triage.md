# Triage: prefect-orchestration-64y — TmuxClaudeBackend

## Summary

Add a third `SessionBackend` implementation (`TmuxClaudeBackend`) in `agent_session.py` that spawns `claude --print` inside a detached tmux session named `po-{issue}-{role}`, so a human operator can `tmux attach` mid-run to watch the agent's output live. The backend must conform to the existing `SessionBackend` Protocol (same verdict-file contract as `ClaudeCliBackend`), tee stdout so the orchestrator still parses `session_id` and result output, support `--resume <uuid>` across turns, and exit cleanly with no orphan tmux sessions.

## Flags

- `has_ui`: **false** — tmux attach is a terminal UX but no application/web UI changes.
- `has_backend`: **true** — new backend class, process spawning, IO plumbing.
- `needs_migration`: **false** — no schema or DB changes.
- `is_docs_only`: **false** — substantive code in `agent_session.py`.

## Risks & Open Questions

- **Stdout capture vs tmux**: tmux captures the PTY; teeing stdout requires either `tmux pipe-pane` or wrapping the command with `script`/`tee` redirection inside the session. Need to confirm which mechanism is used and that it doesn't break the `session_id` parser.
- **Cleanup / orphans**: must ensure session is killed on normal completion, exception, and cancellation. Consider `tmux new-session -d ... \; set remain-on-exit off` plus an explicit `tmux kill-session` in a `finally` block. How to handle pre-existing sessions with the same name (kill? error? reuse?).
- **Resume semantics**: `--resume <uuid>` between turns — does each turn spawn a new tmux session (different window) or reuse the named one? Spec implies persistent named session; need clear per-turn lifecycle.
- **Verdict-file parity**: ensure verdict files are written by the Claude process inside tmux with the same `run_dir` working directory as `ClaudeCliBackend`.
- **Naming collision**: `po-{issue}-{role}` collides across concurrent runs of the same (issue,role); consider including a run/turn suffix or refusing to start when session exists.
- **Environment propagation**: tmux may not inherit the orchestrator's env (e.g., unset `ANTHROPIC_API_KEY`, PATH to `claude` CLI). Needs explicit env passthrough.
- **Exit-code propagation**: getting the Claude process's exit code out of tmux (e.g., via `remain-on-exit on` + `display-message -p '#{pane_dead_status}'`, or sentinel file).
- **Testability**: unit tests need either a tmux stub or a way to assert command construction without actually spawning tmux.
