# Critique: plan-iter-1 for prefect-orchestration-64y

**Verdict: approved (with nits)**

## Fit
Plan matches the issue exactly: a third `SessionBackend` named `TmuxClaudeBackend`, named session `po-{issue}-{role}`, attachable via `tmux attach`, verdict files still land in `run_dir/verdicts/`, resume across turns, no orphans. All six ACs are addressed with concrete verification methods.

## Scope
- Factoring `_build_claude_argv` out of `ClaudeCliBackend` is a small, justified refactor (prevents drift, plan explicitly covers byte-identical regression). Not gold-plating.
- Not gold-plated elsewhere: no retry frameworks, no attach-daemon, no watchdog — appropriate for v1.

## Approach
Grounded in the actual file (`agent_session.py:67-80` argv block, `_UUID_RE` resume semantics, envelope parse at :97-103). tmux strategy is sound:
- `tee` + `PIPESTATUS[0]` + `.rc` sentinel is the right call vs `tmux wait-for` (tmux version skew is real).
- `tmux new-session -d` detached spawn, poll-on-`.rc`, `kill-session` in `finally` — correct.
- Stale-session kill-and-replace aligns with triage.

## AC testability
All six have a mechanical check. AC 1 via `hasattr(..., 'run')` is the right duck-check (Protocol is structural). AC 3 gated via `PO_TMUX_E2E` env flag is pragmatic. AC 4 trick of replacing `start_command` with a shell shim that touches `verdicts/plan.json` is clever and works.

## Risks
Identified: contract preserved, no migrations, tmux availability, name collision documented as "kill and replace", prompt-on-disk trust boundary. Concurrent-same-role follow-up noted but not filed — consider `bd create` before closing.

## Nits (non-blocking)

1. **Sketch return type mismatch.** `return _parse_envelope(stdout, session_id)` must return `tuple[str, str]` per `SessionBackend.run` signature (line 41). Trivial, but spell it out in the helper.
2. **`_clean_env()` in sketch** isn't defined in the plan body. Mention it's a 3-line helper that does `env = os.environ.copy(); env.pop("ANTHROPIC_API_KEY", None); return env`.
3. **Bash `PIPESTATUS` requires bash, not sh.** Plan already uses `bash -lc` — good. Worth a one-line comment in the wrapper explaining why `bash` (not `sh`) is mandatory.
4. **tmux `-x 200 -y 50`**: detached sessions don't need geometry, but it's harmless for when a human attaches — fine to keep.
5. **Prompt cleanup**: plan says "Cleanup of the `.in` file on success" — also clean on failure (put in the same `finally`), or leave explicitly for post-mortem. Pick one and state it.
6. **AC 5 fake-claude test**: asserting `--resume <uuid>` in argv covers the code path, but verify the fake also emits a UUID-shaped `session_id` the first call so the second call has something to resume with. The plan implies this but is worth making explicit.

None of these block implementation.
