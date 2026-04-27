# Plan: prefect-orchestration-stf — verdict-skip nudge

## Goal

When a role agent (triager, planner, critic, verifier, ralph, …) finishes a turn but forgets to write `$RUN_DIR/verdicts/<step>.json`, the orchestrator should detect the missing file post-turn, prompt-inject a single "you forgot to write `<path>`; write it now" nudge that reuses the same session UUID, and only then surface a hard error if the file is still missing. Bound to **one** retry to avoid infinite loops.

## Affected files

- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/agent_session.py` — add `expect_verdict: Path | list[Path] | None` kwarg to `AgentSession.prompt()`; after backend.run returns, if any expected path is missing, send a nudge turn (reusing the new session_id, no fork) and recheck.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/parsing.py` — in `prompt_for_verdict`, pass the expected verdict path to `sess.prompt(..., expect_verdict=<path>)` so the nudge fires before `read_verdict` runs.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_agent_session_verdict_nudge.py` — **new** unit test using a custom backend that omits the verdict on turn 1 and writes it on turn 2 (the nudge); asserts (a) verdict file exists post-prompt, (b) backend was called exactly twice, (c) the second call's prompt contains the verdict path string and the word "forgot"/"write", (d) session_id continuity (turn 2 reuses turn-1's returned sid, no `fork=True`).

## Approach

1. **Core change in `agent_session.py`**:
   - Add a new optional kwarg to `AgentSession.prompt(text, *, fork=False, expect_verdict: Path | None = None)`.
   - After the existing `backend.run(...)` block (and `self.session_id = new_sid`), if `expect_verdict` is set and `expect_verdict.exists()` is False, build a short nudge prompt:
     > `You ended the turn without writing the required verdict file at <abs path>. Write it now using the JSON shape your role prompt specified, then stop. Do not redo the analysis — only emit the file.`
   - Call `self.backend.run(nudge, session_id=self.session_id, cwd=self.repo_path, fork=False, model=self.model, extra_env=extra_env)` once. Update `self.session_id = new_sid`. Mail-injection is **skipped** on the nudge turn (it's a forced internal retry, not a fresh turn) — implement by routing the nudge through a private `_nudge_turn()` that bypasses `_fetch_inbox`/`_mark_read`. Mail mark-read for the original turn still fires on the success path.
   - Telemetry: open a child span `agent.prompt.nudge` with `{role, issue_id, verdict_path}` so a stuck nudge is visible in Logfire.
   - One retry only — no recursion. After the nudge turn returns, fall through; `read_verdict` will raise `FileNotFoundError` if the agent still didn't write it (loud failure, as today).

2. **Parsing wiring (`parsing.py`)**:
   - Compute the expected verdict path once: `expected = verdicts_dir(run_dir) / f"{name}.json"`.
   - Pass it through: `sess.prompt(prompt, fork_session=fork, expect_verdict=expected)` (preserve current `fork_session` kwarg name — see Risks).
   - This is the only place core wires the expectation; pack roles inherit it transparently.

3. **No prompt-template changes in this issue.** Triage flagged a "stronger trailer" as option (2); leaving it out keeps blast radius small and lets the data tell us if the nudge alone closes the failure mode. We can revisit in a follow-up bead.

4. **Backwards compat**: `expect_verdict=None` is the default and preserves today's behaviour exactly. All existing call sites that go through `AgentSession.prompt(text)` directly (build/lint/ralph free-form turns) keep working unchanged.

## Acceptance criteria (verbatim)

> Triager-style verdict-skip failures recover within one nudge cycle; software-dev-full no longer crashes on missing verdict file when the agent's analysis was otherwise complete; behavior covered by a unit test using StubBackend that omits the verdict on first turn

## Verification strategy

- **AC #1 (recovery within one nudge)** — unit test `test_verdict_nudge_recovers_missing_file` (new): drives a stub backend whose first call returns without writing the file, second call writes it; asserts the verdict file exists after `AgentSession.prompt(..., expect_verdict=<p>)` returns and that the backend was invoked exactly twice.
- **AC #2 (no crash on missing verdict when analysis complete)** — unit test `test_prompt_for_verdict_recovers_via_nudge` (new in `test_parsing.py`): uses the same stub-omit-then-write backend; asserts `prompt_for_verdict(...)` returns the parsed verdict dict instead of raising `FileNotFoundError`.
- **AC #3 (test coverage with StubBackend)** — the test uses an inline `_OmitThenWriteBackend` (parallel to `StubBackend`) in `tests/test_agent_session_verdict_nudge.py` so the per-turn behaviour is explicit. Also extend `StubBackend` itself with a `skip_first_n: int = 0` knob so other tests can opt into "first N turns don't write the verdict" without re-implementing the regex sniff. The new test exercises both code paths.
- **Negative case** — `test_verdict_nudge_still_missing_raises_loudly` (new): both turns omit the verdict; assert `prompt_for_verdict` raises `FileNotFoundError` and the backend was called exactly twice (not three+).
- **Session continuity** — assert turn 2's `session_id` arg equals the sid that turn 1 returned, and `fork=False` on both turns (per triage open-question, the nudge must reuse the prior reasoning thread).
- **Mail-injection non-interaction** — assert that when a `mail_fetcher` is wired, the nudge turn does NOT call `mail_fetcher` again (only the original turn does), and `mail_marker` fires exactly once for the original turn's mails.

## Test plan

- **unit** — all changes are pure logic on `AgentSession.prompt` + `prompt_for_verdict`; no Prefect server, no real Claude. Lives in `tests/test_agent_session_verdict_nudge.py` and 1-2 additions to `tests/test_parsing.py`.
- **playwright** — N/A (no UI).
- **e2e** — N/A; the rig has `PO_SKIP_E2E=1`. Real-Claude verification is impractical (the failure is nondeterministic) — covered by stub-backend tests instead.

## Risks

- **Existing baseline failures**: `tests/test_parsing.py::test_prompt_for_verdict_passes_prompt_and_returns_file` and `…fork_forwards_kwarg` are already red in baseline (see `baseline.txt`). The current parsing.py uses `fork_session=True` as the kwarg into `sess.prompt`, but `AgentSession.prompt` accepts `fork=`. This pre-existing API drift is **out of scope** for this bead — preserve the current `fork_session` kwarg in `prompt_for_verdict` so no new regressions land. Add a note to lessons-learned and let a follow-up bead reconcile it. The new tests use a stub session whose `prompt` signature accepts `**kwargs` so they're insensitive to that drift.
- **Mail mark-read semantics**: the original turn's mails must mark-read exactly once even when a nudge fires. Risk: if we naively re-enter `prompt()` for the nudge, `_fetch_inbox` runs again and we double-render mail. Mitigation: route the nudge through `_nudge_turn()` (private helper) that calls `backend.run` directly without touching mail.
- **Telemetry double-counting**: the `_turn_index` counter currently increments per `prompt()` call. Decision: increment again for the nudge turn (it IS a separate model turn) and tag `attrs["nudge"] = True` on the nudge span so dashboards can split them.
- **TmuxInteractiveClaudeBackend interaction**: the interactive backend assigns session ids late (resume mode discovers via sentinel). The nudge turn happens after `new_sid` is set, so the resume-from-prior path works the same way as for any second turn. No special handling required, but worth eyeballing in the build.
- **Verdict path is per-iter (`review-iter-2.json`)**: must use the exact path the orchestrator will read. Computed once in `parsing.py` from `name` — no drift risk.
- **No API contract changes** — `expect_verdict` is a new optional kwarg; no breaking consumers.
- **Self-dev caveat** (from triage): code lives in the same repo the flow is running against. Tests run in pytest subprocess, so module reloads are clean.
