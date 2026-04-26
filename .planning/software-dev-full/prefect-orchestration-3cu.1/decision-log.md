# Decision log — prefect-orchestration-3cu.1 (build iter 1)

- **Decision**: Ship build iter 1 with **no code changes** — audited the
  existing `po-gmail` pack against the 6 ACs and every check came up green.
  **Why**: The pack was substantially complete from a prior iteration of this
  retried run. The plan's stated approach was "audit, patch only on drift,
  re-run smoke loop"; audit found no drift. Adding speculative changes would
  violate the plan's principle §1 callout ("no inventing what already works").
  **Alternatives considered**:
  - Refactor `commands.py` for stylistic cleanup — rejected as scope creep.
  - Bundle a `--bootstrap` consent-flow runner — rejected; SKILL.md
    explicitly defers it to a follow-up bead, and AC list does not require it.
  - Add live-Gmail integration tests — rejected; triage explicitly calls
    live API CI infeasible.

- **AC verification trace** (all green):
  - **AC1 — `google-api-python-client` dep**: present in `po-gmail/pyproject.toml`
    line 7; asserted by `tests/test_pyproject_metadata.py`.
  - **AC2 — SKILL.md CLI-first + vendor links**: `skills/gmail/SKILL.md` opens
    with the 4-tier tool-access ladder (CLI → SDK → HTTP → MCP) and links to
    `developers.google.com/gmail/api/{guides,quickstart/python,auth/scopes}`
    plus the python-client dyn-docs.
  - **AC3 — 3 commands**: `po list` shows `gmail-inbox`, `gmail-send`,
    `gmail-thread`; entry points wired in `pyproject.toml` lines 16-19.
  - **AC4 — 3 doctor checks**: `po doctor` shows three `po-gmail` rows
    (creds-file-present, refresh-token-valid, api-reachable). All return WARN
    (yellow) when creds absent — graceful, not RED — confirmed by
    `tests/test_checks_no_creds.py`.
  - **AC5 — `overlay/CLAUDE.md`**: present, references all three nanocorp
    policy items (from-address via `PO_GMAIL_FROM`, label conventions via
    cross-reference to SKILL.md, do-not-touch via `PO_GMAIL_DO_NOT_TOUCH`).
  - **AC6 — editable install**: re-ran `po install --editable
    /home/ryan-24/Desktop/Code/personal/nanocorps/po-gmail` — exit 0; `po list`
    and `po doctor` both surface the pack's contributions.

- **Decision**: Did **not** acquire `mcp-agent-mail` file reservations.
  **Why**: No files were modified. Reservations are required only before
  edits per the convention doc §"File reservations". An audit-only iteration
  has no collision surface.
  **Alternatives considered**: Reserve preemptively to register intent —
  rejected as ceremony with no risk reduction.
