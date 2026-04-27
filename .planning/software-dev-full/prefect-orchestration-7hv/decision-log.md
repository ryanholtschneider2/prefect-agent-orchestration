# Decision log — prefect-orchestration-7hv

- **Decision**: Included `argv: {cmd}` in addition to `stdout[:2000]` in the RuntimeError message.
  **Why**: Issue DESIGN section explicitly suggests "log the rendered argv … so reproducing is one copy-paste". `cmd` comes from `_build_claude_argv` and contains no secrets (env/prompt are passed separately), so safe to include.
  **Alternatives considered**: stdout-only (matches AC1 minimum but loses the easy repro hint). Rejected.

- **Decision**: Patched `prefect_orchestration.agent_session.subprocess.run` rather than spawning a fake `claude` binary on PATH.
  **Why**: Pure unit test per CLAUDE.md test-layer guidance — mocking subprocess belongs in `tests/` (unit), not `tests/e2e/`.
  **Alternatives considered**: e2e with a stub `claude` script — rejected as overkill for a string-format change.
