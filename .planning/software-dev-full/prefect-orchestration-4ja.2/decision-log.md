# Decision Log — prefect-orchestration-4ja.2

- **Decision**: Inject mail via two callable fields (`mail_fetcher`, `mail_marker`) on `AgentSession` rather than importing `po_formulas.mail` from core.
  **Why**: Core (`prefect-orchestration`) must work without the sibling pack installed (CLAUDE.md "Working on this repo" + plan §1). Avoids a layering inversion.
  **Alternatives considered**: lazy `importlib.import_module("po_formulas.mail")` inside `prompt()` — rejected because it couples core to a specific pack name and complicates testing.

- **Decision**: Inbox cap (`MAX_INBOX_MESSAGES = 20`) sorted by `created_at` desc, applied only when fetcher returns more than the cap.
  **Why**: Triage flagged token-budget risk; not in ACs but cheap to add (plan §6).
  **Alternatives considered**: no cap (unbounded prompt growth); pass-through cap configurable per-session — deferred until a need arises.

- **Decision**: `_render_with_inbox` uses `getattr(..., default)` lookups against the mail object rather than a strict isinstance/typed contract.
  **Why**: Core never imports the pack's `Mail` dataclass. Duck-typed access keeps the cross-package contract minimal: `.id`, `.subject`, `.body`, optional `.from_agent`, optional `.created_at`.
  **Alternatives considered**: define a Protocol in core (`MailLike`) — overkill for a 4-attribute object; would still be duck-typed at runtime.

- **Decision**: Fetcher exceptions are caught and logged; mark_read exceptions are caught per-id.
  **Why**: Mail is auxiliary metadata; a transient `bd` failure must not abort the agent turn (plan §4).
  **Alternatives considered**: let exceptions propagate — would couple turn success to mail subsystem health, violating the spirit of "leave unread on failure" (which targets backend failure, not mail-layer failure).

- **Decision**: `session_id` is updated only AFTER `backend.run` returns successfully (before `_mark_read`).
  **Why**: Matches prior behavior — failed turns must not advance the session pointer. Also tested explicitly.

- **Decision**: Pack-side wiring (passing `po_formulas.mail.inbox` / `mark_read` into `AgentSession`) is out of scope for this issue.
  **Why**: Plan §"Risks" notes the follow-up belongs in `po-formulas-software-dev`. Core hook is the deliverable here.
