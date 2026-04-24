# Plan — prefect-orchestration-4ja.2

## Affected files

- `prefect_orchestration/agent_session.py` — wrap `AgentSession.prompt()` to inject mail; add `skip_mail_inject` flag.
- `tests/test_agent_session_mail.py` (new) — unit tests for inject + mark-read semantics.
- `CLAUDE.md` — document the auto-inject behavior.
- (No edits to `po_formulas/mail.py`; we only consume its public API.)

## Approach

1. **Inbox fetcher injection (avoid layering inversion).** Core can't hard-import `po_formulas.mail` (the pack lives in a sibling repo and core must work without it). Solution: add a callable attribute on `AgentSession`:

   ```python
   mail_fetcher: Callable[[str], list] | None = None  # signature: (role) -> list[Mail-like]
   mail_marker:  Callable[[str], None] | None = None  # signature: (mail_id) -> None
   ```

   Default `None` → no injection (core stays pack-agnostic). Callers in `po-formulas-software-dev` wire these to `po_formulas.mail.inbox` and `po_formulas.mail.mark_read` when constructing the session.

   Alternative considered: lazy `importlib.import_module("po_formulas.mail")` inside `prompt()`. Rejected — couples core to a pack name and makes tests harder. Callable-injection is cleaner and matches the existing `SessionBackend` Protocol pattern.

2. **`prompt()` wrap.** Reshape:

   ```python
   def prompt(self, text: str, *, fork: bool = False) -> str:
       mails = self._fetch_inbox()  # [] when skip_mail_inject or no fetcher
       full_text = self._render_with_inbox(mails, text)  # passthrough if mails empty
       try:
           result, new_sid = self.backend.run(full_text, ...)
       except Exception:
           raise  # leave mail unread on failure (AC3)
       self.session_id = new_sid
       self._mark_read(mails)  # only after successful return (AC2)
       return result
   ```

   - `_fetch_inbox` returns `[]` when `skip_mail_inject` is True or `mail_fetcher is None` or call raises (defensive — mail is auxiliary, must not break the turn).
   - `_render_with_inbox` returns `text` unchanged when `mails` is empty (AC4 — no dead tag).
   - When non-empty, render exactly the format from the issue:

     ```
     <mail-inbox>
     [<created_at iso> | from=<from_agent or '?'>] subject: <subject>
     <body>
     ---
     [...]
     </mail-inbox>

     <original prompt>
     ```

   - IDs captured at fetch-time are the only ones marked read (AC: "concurrent mail mid-turn must not be auto-marked"). New mail arriving during the turn is naturally excluded.

3. **`skip_mail_inject` flag.** New dataclass field `skip_mail_inject: bool = False`. StubBackend / dry-run flow construction sites pass `skip_mail_inject=True` to keep dry-runs cheap (no `bd list` shell-out). Triage notes this in §"Dry-run / stub paths".

4. **Failure semantics.** "Success" = `backend.run` returned without exception. Any exception propagates and `mark_read` is *not* called → mail stays open for the next turn (AC3). Exceptions inside `mark_read` itself are caught + logged (mail-marker failure must not poison a successful turn).

5. **Role attribute.** `AgentSession.role` already exists (line 358) — no plumbing needed.

6. **Token budget guard (defensive, not in ACs).** Cap rendered inbox at top-N (configurable, default e.g. 20 most recent) to avoid prompt bloat. Flagged in triage; cheap to add. Will use a constant `MAX_INBOX_MESSAGES = 20`, sort by `created_at` desc, truncate.

## Acceptance criteria (verbatim from issue)

1. Unread mail for the agent's role is prepended to every prompt as an `<mail-inbox>` XML block.
2. On turn success, those messages transition to read.
3. On turn failure, they remain unread.
4. Empty inbox = no block prepended (no dead tag).
5. Test covers both empty-inbox and with-messages paths.
6. Documented in CLAUDE.md.

## Verification strategy

| AC | How verified |
|----|--------------|
| 1  | Unit test: stub `mail_fetcher` returns 2 fake `Mail` objects → capture prompt passed to a recording backend → assert `<mail-inbox>` block present, contains both subjects/bodies, ends before original prompt text. |
| 2  | Unit test: same setup, after `prompt()` returns, assert the recorded `mark_read` calls equal the IDs returned by the fetcher. |
| 3  | Unit test: backend raises → `pytest.raises` catches → assert `mark_read` was NOT called. |
| 4  | Unit test: fetcher returns `[]` → assert prompt forwarded verbatim (no `<mail-inbox>` substring). |
| 5  | Both paths covered above (tests #1/#2 and #4). |
| 6  | Manual diff of `CLAUDE.md` — new subsection under the AgentSession/PO behavior area. |

## Test plan

- **Unit only.** New file `tests/test_agent_session_mail.py`:
  - `RecordingBackend` (stub backend that captures the prompt arg and the resolved session_id; configurable to raise).
  - `make_mail(...)` helper for `Mail`-like objects (use a `SimpleNamespace` since core never imports the pack's `Mail` dataclass — the contract is just attribute access on `.id`, `.subject`, `.body`, `.from_agent`, `.created_at`).
  - Tests:
    - `test_empty_inbox_no_block_prepended`
    - `test_nonempty_inbox_renders_block_and_preserves_prompt`
    - `test_successful_turn_marks_messages_read`
    - `test_failed_turn_leaves_messages_unread`
    - `test_skip_mail_inject_bypasses_fetcher` (verifies fetcher not called)
    - `test_concurrent_mail_not_auto_marked` (fetcher called once, second call would return more — only first batch's IDs marked)
- **No e2e change required** — pack-side wiring of fetchers is a separate beads issue (would belong in `po-formulas-software-dev`); this task only owns the core hook.
- **No playwright** — no UI.

## Risks

- **Layering risk** — core importing pack module. Mitigated by callable-injection (no import).
- **Token bloat** — large inboxes. Mitigated by `MAX_INBOX_MESSAGES` cap.
- **`mark_read` partial failure** — if `mark_read` raises mid-loop after marking some IDs, remaining mail stays open and will reappear next turn (idempotent — duplicates are tolerable since render is read-only).
- **API contract change** — `AgentSession` gains two new optional fields and one new flag, all defaulted, all backwards-compatible. No existing callers break.
- **No migrations / no API surface change** to PO CLI.
- **Pack wiring is out of scope** — agents in production won't see auto-injected mail until a follow-up issue plumbs `mail_fetcher`/`mail_marker` from the pack's flow construction sites. Will note this in the lessons-learned / decision-log.
