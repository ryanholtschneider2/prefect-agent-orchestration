# Decision log — prefect-orchestration-7hv

- **Decision**: Included `argv: {cmd}` in addition to `stdout[:2000]` in the RuntimeError message.
  **Why**: Issue DESIGN section explicitly suggests "log the rendered argv … so reproducing is one copy-paste". `cmd` comes from `_build_claude_argv` and contains no secrets (env/prompt are passed separately), so safe to include.
  **Alternatives considered**: stdout-only (matches AC1 minimum but loses the easy repro hint). Rejected.

- **Decision**: Patched `prefect_orchestration.agent_session.subprocess.run` rather than spawning a fake `claude` binary on PATH.
  **Why**: Pure unit test per CLAUDE.md test-layer guidance — mocking subprocess belongs in `tests/` (unit), not `tests/e2e/`.
  **Alternatives considered**: e2e with a stub `claude` script — rejected as overkill for a string-format change.

## Builder iter 1 (post-plan-approval)

- **Decision**: No code changes in this build iteration — the implementation in commit 429a360 (already on master) satisfies all three acceptance criteria.
  **Why**: AC1 (`stdout[:2000]` in non-zero RuntimeError) lands at agent_session.py:166–170; AC2 (regression test in tests/test_agent_session.py) lands in the same commit (~59 lines); AC3 (no behavior change on success) — the new format string is gated on `proc.returncode != 0`, success path untouched. Re-doing the work would be churn.
  **Alternatives considered**: Re-applying the change on top of itself (no-op diff) or refactoring (out of scope per plan).
