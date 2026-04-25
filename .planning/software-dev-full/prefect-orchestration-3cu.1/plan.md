# Plan: prefect-orchestration-3cu.1 — `po-gmail` tool pack

## Affected files

New pack created **outside this rig**, as a sibling directory:
`/home/ryan-24/Desktop/Code/personal/nanocorps/po-gmail/` (per
principle §pw4 — pack-contrib code lives in its own repo, not in the
caller's rig). Layout:

```
po-gmail/
├── pyproject.toml
├── README.md
├── po_gmail/
│   ├── __init__.py
│   ├── auth.py            # OAuth creds resolver (path + load + refresh)
│   ├── service.py         # build googleapiclient `service` from creds
│   ├── commands.py        # gmail-inbox / gmail-send / gmail-thread callables
│   └── checks.py          # 3 DoctorCheck callables
├── skills/
│   └── gmail/
│       └── SKILL.md       # CLI-first usage + nanocorp rules + vendor links
└── overlay/
    ├── CLAUDE.md          # agent-facing reinforcement of skill rules
    └── .env.example       # documents PO_GMAIL_CREDS / _TOKEN / _FROM env vars
```

Nothing in the rig (`prefect-orchestration/`) is modified. Verification
is done from the rig by running `po install --editable
/home/ryan-24/Desktop/Code/personal/nanocorps/po-gmail`, then `po
update`, then `po list`, `po doctor`, `po gmail-inbox --help` etc.

## Approach

Mirror the shape of `software-dev/po-formulas/` exactly:

1. **`pyproject.toml`** — `name = "po-gmail"`, `requires-python = ">=3.11"`,
   `dependencies = ["google-api-python-client>=2.0",
   "google-auth>=2.0", "google-auth-oauthlib>=1.0",
   "prefect-orchestration"]` (last for `DoctorCheck` import only).
   Three `po.commands` entries and three `po.doctor_checks` entries
   pointing into `po_gmail.commands` / `po_gmail.checks`. Hatchling
   build backend, `[tool.hatch.build.targets.wheel] packages = ["po_gmail"]`.
   `[tool.uv.sources]` pins core editable to `../prefect-orchestration`
   for local dev.

2. **`po_gmail/auth.py`** — single source of truth for resolving creds.
   Reads `PO_GMAIL_CREDS` (default `~/.config/po-gmail/credentials.json`)
   and `PO_GMAIL_TOKEN` (default `~/.config/po-gmail/token.json`). Loads
   `google.oauth2.credentials.Credentials.from_authorized_user_file`,
   refreshes via `google.auth.transport.requests.Request` if expired and
   a refresh token exists, persists rotated token back to disk. Returns
   `Credentials` or raises a typed `GmailAuthError` with an actionable
   hint string. Doctor checks reuse this loader.

3. **`po_gmail/service.py`** — `build_service() -> Resource` calls
   `googleapiclient.discovery.build("gmail", "v1", credentials=...,
   cache_discovery=False)`. Thin so commands and checks share it.

4. **`po_gmail/commands.py`** — three callables registered as
   `po.commands`. Signatures match core's `commands.parse_args` (kwargs
   coerced from `--key value`):

   - `gmail_inbox(max_results: int = 20, label: str = "INBOX",
     unread_only: bool = True) -> None` — `users.messages.list` with
     `q="is:unread"` (when set) + `labelIds=[label]`. Hydrates each via
     `users.messages.get(format="metadata", metadataHeaders=["From",
     "Subject","Date"])` and prints a `id  date  from  subject` table.
   - `gmail_send(to: str, subject: str, from_addr: str | None = None,
     thread_id: str | None = None, dry_run: bool = True) -> None` —
     reads body from **stdin** (per the issue), enforces `dry_run=True`
     by default (prints the assembled MIME + recipients and exits 0
     without calling the API), executes `users.messages.send` only when
     `--no-dry-run` is passed. Resolves `from_addr` from arg, else
     `PO_GMAIL_FROM` env, else fails with an actionable error
     referencing the SKILL nanocorp rules. Refuses to send into any
     label in `PO_GMAIL_DO_NOT_TOUCH` (comma-list, default empty).
   - `gmail_thread(thread_id: str, format: str = "full") -> None` —
     `users.threads.get`, prints each message's headers + decoded body
     (text/plain part) in chronological order.

   All commands call `auth.load_creds()` lazily inside the function so
   `po list` / `po show` work without creds present. Errors print a
   one-line hint and `raise SystemExit(2)`.

5. **`po_gmail/checks.py`** — three `DoctorCheck` callables (zero-arg,
   return `DoctorCheck`):

   - `creds_file_present()` — green if `PO_GMAIL_CREDS` path exists +
     readable + parses as JSON with `installed`/`web` key; yellow if env
     unset (default path missing); red if path is set but file is
     missing/unreadable. Hint links to
     https://developers.google.com/gmail/api/quickstart/python.
   - `refresh_token_valid()` — loads token JSON; if missing → yellow
     ("run `po gmail-inbox --max-results 1` to bootstrap auth"); if
     present and `Credentials` object reports `expired and refresh_token`
     → attempts in-process refresh under a 4 s timeout, green on
     success, red on failure with the API error.
   - `api_reachable()` — calls `users.getProfile(userId="me")` under a
     4 s timeout; green w/ email returned, yellow on transient
     `HttpError 5xx`, red on auth failure. Skips (yellow "skipped — no
     creds") when `creds_file_present` would be red, so the table reads
     top-down and doesn't double-report the same root cause.

   Each check wraps its body in `try/except Exception` and returns a
   yellow `DoctorCheck` rather than letting an uncaught exception abort
   the table — core has a 5 s soft timeout but only catches
   `subprocess.TimeoutExpired`-class issues.

6. **`skills/gmail/SKILL.md`** — YAML frontmatter (`name: gmail`,
   `description:` one-liner). Body sections:
   - **Tool-access order** — start with the `po gmail-*` commands, then
     SDK fallback (link
     https://developers.google.com/gmail/api/guides/sending and
     https://googleapis.github.io/google-api-python-client/docs/dyn/gmail_v1.html),
     then HTTP API (last). Honest note that no first-party Gmail CLI
     exists; `po gmail-*` is the CLI surface backed by the SDK.
   - **Nanocorp rules** — from-address policy ("send from
     `$PO_GMAIL_FROM`; never `noreply@`; never the personal address";
     references env var, not literal addresses), label conventions
     (`po/`, `po/dispatched`, `po/needs-human`), do-not-touch folders
     (`Personal/`, anything under `Legal/`, `Important`).
   - **Send safety** — `gmail-send` defaults to `--dry-run`; pass
     `--no-dry-run` only after the body has been reviewed; never send
     bulk (>5 recipients) without `bd human` approval.
   - **Vendor doc pointers** — Gmail API guides, OAuth quickstart,
     scope reference. No `llms.txt` is published by Google for Gmail
     (verified via search) so we link the human docs.

7. **`overlay/CLAUDE.md`** — short reinforcement: "If a task involves
   reading or sending mail, prefer `po gmail-*` over MCP. Run `po
   doctor` first; if creds missing follow SKILL bootstrap. Honor the
   from-address and do-not-touch rules in `skills/gmail/SKILL.md`."

8. **`overlay/.env.example`** — documents `PO_GMAIL_CREDS`,
   `PO_GMAIL_TOKEN`, `PO_GMAIL_FROM`, `PO_GMAIL_DO_NOT_TOUCH`. Real
   secrets stay out.

## Acceptance criteria (verbatim)

1. Python dep: `google-api-python-client`
2. `skills/gmail/SKILL.md` CLI-first with vendor doc links
3. 3 commands
4. 3 doctor checks
5. `overlay/CLAUDE.md`
6. Works with `po install --editable /path`

## Verification strategy

| AC | Concrete check |
|----|----------------|
| 1  | `grep -q '"google-api-python-client' po-gmail/pyproject.toml`; after `po install --editable`, `uv pip show google-api-python-client` (in `po`'s tool venv) returns a version. |
| 2  | `head -1 skills/gmail/SKILL.md` is `---` (frontmatter); `grep -c "developers.google.com" skills/gmail/SKILL.md` ≥ 2; `grep -i "po gmail-" skills/gmail/SKILL.md` precedes the SDK section (CLI-first ordering). |
| 3  | `po list` (run from a rig with the pack installed) shows `gmail-inbox`, `gmail-send`, `gmail-thread` rows with `KIND=command`. `po show gmail-send` prints its docstring. |
| 4  | `po doctor` emits 3 rows whose `SOURCE` column is the `po-gmail` distribution (`creds file present`, `refresh token valid`, `api reachable`). Verified by greppable substrings in stdout. With no creds present they should be red/yellow but never crash the table. |
| 5  | After `po install --editable …` and a fresh `AgentSession()` turn (or a manual `python -c` that triggers the overlay walk), `<rig>/CLAUDE.md` exists if absent, or is left alone if present (skip-existing semantics from `4ja.4`). For a no-touch verification: `test -f po-gmail/overlay/CLAUDE.md`. |
| 6  | `po install --editable /home/ryan-24/Desktop/Code/personal/nanocorps/po-gmail` exits 0; `po update` exits 0; `po packs` lists `po-gmail` with its three commands and three checks attributed to it. |

## Test plan

Test layers that apply:

- **Unit tests** (in `po-gmail/tests/`, run with `uv run python -m
  pytest`):
  - `test_pyproject_metadata.py` — parse `pyproject.toml`, assert
    dependency + entry-point names exactly match the AC.
  - `test_commands_signatures.py` — `import po_gmail.commands`, assert
    `gmail_inbox`, `gmail_send`, `gmail_thread` are callable with the
    documented kwargs (use `inspect.signature`).
  - `test_commands_dryrun.py` — monkeypatch `po_gmail.service.build_service`
    to return a `MagicMock`; call `gmail_send(to="a@b", subject="x",
    dry_run=True)` with stdin piped, assert no API method invoked and
    output contains the assembled MIME headers.
  - `test_checks_no_creds.py` — set `PO_GMAIL_CREDS` to a missing path,
    call all three checks, assert each returns a `DoctorCheck` with
    non-green status and a hint string (no exception raised).
  - `test_skill_and_overlay.py` — assert `skills/gmail/SKILL.md` and
    `overlay/CLAUDE.md` exist, frontmatter parses, vendor links present.

- **e2e** (in this rig's `tests/e2e/` — gated on `po-gmail` being
  importable so CI without the sibling pack still passes; skip with
  `pytest.importorskip("po_gmail")`):
  - `test_po_gmail_pack_install.py` — runs `po install --editable
    <abs-path>` in a tmp HOME, then `po list`, asserts the three
    commands appear with `kind=command`; runs `po doctor` and asserts
    the three pack-contributed rows appear; runs `po uninstall
    po-gmail` to clean up.

- **playwright** — N/A (no UI).

## Risks

- **No git remote / no CI for the new pack repo** — the pack lives in a
  fresh sibling directory; first commit is local-only. Document this in
  the pack's README; no migration impact.
- **OAuth bootstrap is interactive** — the pack does **not** ship an
  installed-app flow runner in this issue. Doctor checks degrade
  gracefully (yellow) when token is absent; SKILL.md tells the agent
  to bootstrap manually via the Google quickstart. A follow-up bead
  can add `po gmail-bootstrap-auth`.
- **`googleapiclient` `cache_discovery=False`** is required to avoid a
  warning on Python 3.13; covered in `service.py`.
- **API contract**: this pack adds *new* commands; nothing in core or
  `po-formulas-software-dev` is renamed, so no existing consumers
  break. `po install` will refuse the pack if any of its `po.commands`
  shadow a core verb — none of `gmail-inbox/-send/-thread` collide
  with current verbs (`run`/`list`/`show`/`deploy`/`logs`/`artifacts`/
  `sessions`/`watch`/`retry`/`status`/`doctor`/`install`/`update`/
  `uninstall`/`packs`).
- **Send safety** — `gmail-send` defaults to `--dry-run` and refuses
  do-not-touch labels; mitigates accidental autonomous send. Recipient
  cap and human-approval threshold are documented in SKILL.md but not
  enforced in code (deferred to a future "guarded send" bead).
- **Secrets hygiene** — neither overlay nor SKILL.md hardcodes
  addresses, tokens, or creds paths beyond env-var documentation.
