# Decision log — prefect-orchestration-stf

- **Decision**: Plumbed nudge logic via a new `expect_verdict: Path | None` kwarg on `AgentSession.prompt`, with `prompt_for_verdict` wiring it.
  **Why**: Triage flagged that the *expected verdict path* is a pack/caller concern (per role) while the nudge mechanic is a core concern (every backend benefits). A single optional kwarg keeps the contract minimal and makes the legacy code path (`expect_verdict=None`) byte-identical.
  **Alternatives considered**: hardcoding role→verdict map in core (rejected: violates "no role names in core" rule); event-style hook on `AgentSession` (rejected: overkill for one verb); rendering a forced trailer into every prompt template (option 2 in the issue — left as a future bead so we can measure whether the nudge alone closes the failure mode).

- **Decision**: Mail-injection is **not** re-fetched for the nudge turn — `_nudge_for_verdict` calls `backend.run` directly, bypassing `_fetch_inbox` / `_mark_read`.
  **Why**: The nudge is a forced internal retry, not a fresh turn. Re-fetching would double-render `<mail-inbox>` and risks double-marking on success.
  **Alternatives considered**: re-entering `prompt()` recursively (rejected: re-fetches mail and is harder to bound to one retry); marking nudge mails as a separate set (rejected: extra state, no semantic gain).

- **Decision**: Cap nudges at exactly **one** retry. After the nudge, if the file is still missing we fall through and let the caller's `read_verdict` raise `FileNotFoundError`.
  **Why**: Triage explicitly required a hard cap to prevent infinite loops (sandbox / permissions / wrong path). Loud failure beats silent loop.
  **Alternatives considered**: configurable retry count (rejected: YAGNI, one is enough per the failure-mode pattern); silent default verdict (rejected: violates `parsing.py` "loud failure" docstring).

- **Decision**: Telemetry — increment `_turn_index` on the nudge and emit a separate `agent.prompt.nudge` span tagged with `nudge=True` and `verdict_path`.
  **Why**: A nudge IS a separate model turn; under-counting would skew turn-count metrics. A distinct span name lets dashboards split nudges out from regular turns.
  **Alternatives considered**: nest-as-child of `agent.prompt` (rejected: span lifecycle ends before the nudge fires); reuse `agent.prompt` with an attribute (rejected: harder to query "show me only nudges").

- **Decision**: Preserved `prompt_for_verdict`'s pre-existing `fork_session=True` kwarg name when calling `sess.prompt`, even though `AgentSession.prompt` accepts `fork=`. Wrapped the call in `try/except TypeError` so legacy stubs that don't accept `expect_verdict` still work.
  **Why**: The `fork_session` vs `fork` API drift is pre-existing (baseline test failures `test_prompt_for_verdict_fork_forwards_kwarg` confirm it predates this issue). Reconciling it here would balloon scope and risk new regressions; queue it for a follow-up bead.
  **Alternatives considered**: rename to `fork=` here (rejected: out of scope, breaks the existing test expectations); make `_StubSession` in the existing test accept `expect_verdict` (rejected: would mask the legacy compatibility we want to preserve).

- **Decision**: Added a stand-alone `_OmitThenWriteBackend` test fixture in `tests/test_agent_session_verdict_nudge.py` rather than extending the in-tree `StubBackend`.
  **Why**: The plan suggested a `skip_first_n` knob on `StubBackend`, but `StubBackend` sniffs the prompt for the verdict path via regex while the nudge prompt also contains the path — adding a "skip" knob plus the regex made test intent muddier. A dedicated 30-line backend that explicitly tracks turns + writes-or-not is grep-able and matches "schemas-are-for-the-consumer" thinking.
  **Alternatives considered**: extending `StubBackend` (rejected: bigger blast radius, conflated production stub vs test-only fixture).

- **Decision**: Test `test_verdict_nudge_skips_mail_reinjection` asserts the mail fetcher fires exactly once across the original-turn-plus-nudge cycle.
  **Why**: Triage called this out as an open question (mark-read semantics on retry path). Pinning the contract in a test prevents regression.
