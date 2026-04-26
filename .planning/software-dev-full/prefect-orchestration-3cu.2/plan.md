# Plan: prefect-orchestration-3cu.2 — po-gcal tool pack

## Scope

Stand up a new sibling pack `po-gcal` (NOT inside `prefect-orchestration/`)
that contributes Google Calendar tooling to PO-managed agents via entry
points. Three `po.commands`, two `po.doctor_checks`, one skill markdown,
one overlay snippet. Pure SDK-based (no `gcloud`/`gcal` shell-out — see
Risks).

## Affected files

New pack at `/home/ryan-24/Desktop/Code/personal/nanocorps/po-gcal/`:

```
po-gcal/
├── pyproject.toml              # hatchling; deps: prefect-orchestration,
│                               #   google-api-python-client, google-auth,
│                               #   google-auth-oauthlib
│                               # entry-points: po.commands (3), po.doctor_checks (2)
│                               # wheel packages: po_gcal, overlay, skills
├── README.md
├── .gitignore
├── po_gcal/
│   ├── __init__.py
│   ├── _client.py              # auth seam: resolve_creds(), build_service()
│   │                           # CredsMissing / CredsInvalid exceptions
│   ├── commands.py             # gcal_today / gcal_create / gcal_free
│   └── checks.py               # creds_present / calendar_reachable
├── overlay/
│   └── CLAUDE.md               # wrapped in <!-- po-gcal:begin/end --> markers
├── skills/
│   └── gcal/SKILL.md           # CLI-first, frontmatter, command examples,
│                               #   auth troubleshooting, Google API doc links
└── tests/
    ├── conftest.py             # FakeService + monkey-patch fixtures
    ├── test_commands.py        # ~12 tests
    └── test_checks.py          # ~7 tests
```

Touched in this rig: append decisions to
`.planning/software-dev-full/prefect-orchestration-3cu.2/decision-log.md`.

## Approach

**Auth (`_client.py`)** — single SDK seam used by all commands and the
reachability check. Resolution order:
1. `GOOGLE_APPLICATION_CREDENTIALS` env (service-account JSON path).
2. ADC via `google.auth.default()` (picks up `gcloud auth
   application-default login`).
3. Well-known file fallback (`~/.config/gcloud/application_default_credentials.json`).

Raises `CredsMissing` (none of the above resolved) or `CredsInvalid`
(found but malformed/expired). `build_service(api="calendar",
version="v3")` returns a discovery client built from the resolved
credentials. Translates SDK exceptions to `CredsInvalid`/`CredsMissing`
so callers handle uniformly.

**Commands (`commands.py`)** — Typer-free; plain Python callables matching
the `po.commands` contract (kwargs in, prints to stdout, exits non-zero
on error via `SystemExit(2)`).

- `gcal_today(calendar="primary")` — RFC-3339 today window via local
  midnight → next-midnight; `events().list(calendarId, timeMin,
  timeMax, singleEvents=True, orderBy="startTime")`. Prints JSON list
  of `{id, summary, start, end, attendees}`.
- `gcal_create(calendar="primary")` — reads JSON Event resource from
  stdin (validates non-empty, valid JSON, dict shape; exits 2 on any
  failure with stderr message). Calls `events().insert(calendarId,
  body)`, prints `{id, htmlLink}`.
- `gcal_free(user, start, end, calendar=None)` — `user`/`start`/`end`
  required (no defaults — predictability over convenience). Queries
  `freebusy().query(body={timeMin, timeMax, items=[{id: item_id}]})`,
  prints `{user, calendar, busy}`. `--calendar` overrides; default
  uses `user` as the calendar id.

Helpers: `_die(msg, code=2)`, `_service()` wraps `build_service`,
`_wrap_api()` collapses any SDK error into a clean exit-2 with a
stderr message.

**Doctor checks (`checks.py`)** — two `po.doctor_checks` callables
returning `prefect_orchestration.doctor.DoctorCheck`:

- `creds_present()` — green when `resolve_creds` succeeds; red on
  `CredsMissing`; yellow on `CredsInvalid` (with hint pointing to the
  three resolution paths).
- `calendar_reachable()` — short-circuits **yellow** when creds
  missing/invalid (avoid double-counting the red from `creds_present`).
  Then `calendarList().list(maxResults=1).execute()`. Green on success;
  red on any other exception.

Each check wrapped in the 5-second soft timeout that `po doctor`
already imposes — no extra timeout plumbing needed.

**Overlay (`overlay/CLAUDE.md`)** — short snippet (≤30 lines) wrapped
in `<!-- po-gcal:begin -->` / `<!-- po-gcal:end -->` markers so
re-application is idempotent. Points agents at the three commands and
`po doctor` for auth troubleshooting. Auto-merged into rig cwd by
`pack_overlay.py` (per `4ja.4`).

**Skill (`skills/gcal/SKILL.md`)** — frontmatter (`name`, `description`,
`when_to_invoke`); sections "When to invoke", "Commands" (each command
with one-line description + example invocation), "Auth troubleshooting"
(env var, ADC, well-known file), "Reference" (links:
`developers.google.com/calendar/api/v3/reference`, quickstart Python,
Event resource schema, freebusy reference, `developers.google.com/llms.txt`
best-effort). CLI-first per AC#2 — examples are `po gcal-today` etc.,
not Python snippets.

**Pyproject.toml** scaffolding mirrors `software-dev/po-formulas/`
(hatchling backend; `[tool.hatch.build.targets.wheel] packages = ["po_gcal",
"overlay", "skills"]` so overlay+skills ship in the wheel; `[tool.uv.sources]`
points at editable `prefect-orchestration` for local dev).

**Tests** — two-file split, all SDK calls mocked at the
`_client.build_service` seam (NOT inside googleapiclient internals —
mocking at our seam stays robust to SDK upgrades).

## Acceptance criteria (verbatim)

(1) dep: google-api-python-client; (2) skills/gcal/SKILL.md CLI-first
with doc links; (3) 3 commands; (4) 2 doctor checks; (5) overlay/CLAUDE.md.

## Verification strategy

| AC | Concrete check |
|----|---|
| 1 | `grep '"google-api-python-client' po-gcal/pyproject.toml` returns the dep line; `uv sync` resolves it. |
| 2 | `cat po-gcal/skills/gcal/SKILL.md` — has frontmatter, lists `po gcal-today/-create/-free` with example invocations, includes ≥3 links to `developers.google.com/calendar/api/v3/...`. |
| 3 | `python -c "from importlib.metadata import entry_points; print([e.name for e in entry_points(group='po.commands') if e.dist.name=='po-gcal'])"` returns `['gcal-today','gcal-create','gcal-free']`. After install, `po list` shows all three with `KIND=command, SOURCE=po-gcal`. |
| 4 | Same EP introspection for `group='po.doctor_checks'` returns `['gcal-creds-present','gcal-reachable']`. `po doctor` after install shows two new rows under SOURCE=po-gcal. |
| 5 | `cat po-gcal/overlay/CLAUDE.md` exists, has `<!-- po-gcal:begin -->` markers; `pack_overlay.apply(...)` test (or manual smoke) confirms it merges into a rig's `CLAUDE.md` idempotently. |

## Test plan

**Unit (pytest, in pack repo)** — primary coverage; ~19 tests.
- `test_commands.py`: gcal_today happy path; default calendar branch;
  exit-2 on no creds; gcal_create posts stdin JSON; rejects empty,
  non-JSON, non-object stdin (3 parametrized); gcal_free returns busy
  blocks; calendar override; param validation (3 parametrized cases for
  missing user/start/end); generic API error collapsed to exit 2.
- `test_checks.py`: creds_present red/yellow/green; calendar_reachable
  short-circuit yellow when no creds; red on API error; green on
  success; resolve_creds raises CredsInvalid when env path missing.

**E2E** — none in this issue. Live API tests (calling real Google) are
out of scope; gated behind creds in a future issue if ever wanted.

**Playwright** — N/A (no UI).

**Smoke after install** — `po list` shows 3 commands; `po doctor`
shows 2 new check rows; `po gcal-today --help` does not raise.

## Risks

- **Auth ambiguity (resolved)** — Decided SDK-only with
  service-account JSON + ADC fallback. No `gcloud`/`gcal` shell-out:
  `gcloud` has no native calendar verbs, and `gcal`/`khal` aren't
  ubiquitous; headless agents can't run interactive OAuth.
  Documented in decision log.
- **gcal-create input format (resolved)** — JSON-on-stdin (Event
  resource shape from the v3 API). Handles nested objects cleanly,
  matches API contract directly.
- **gcal-free param shape (resolved)** — Required `--start` and `--end`
  (RFC-3339), no defaults. Predictability beats convenience for a
  command an LLM will call.
- **Wheel packaging of overlay+skills** — hatchling needs explicit
  `packages` listing for top-level non-Python dirs. Confirmed pattern
  works in `po-slack` / `po-formulas-software-dev`.
- **Entry-point collision** — `po install`'s post-install scan rejects
  packs whose `po.commands` shadow core verbs. `gcal-*` names don't
  collide with any core Typer verb (`run`, `list`, `show`, `deploy`,
  `logs`, `artifacts`, `sessions`, `watch`, `retry`, `status`,
  `doctor`, `install`, `update`, `uninstall`, `packs`).
- **Pack location** — Lives in sibling repo, NOT in
  `prefect-orchestration/`. Builder must `cd ../po-gcal` for any code
  edits and commit there; only the decision log lands in this rig.
- **No migrations / no API contract changes** — pure additive pack,
  no breakage to existing consumers.
- **mcp-agent-mail file reservations** — pack lives outside the
  registered project workspace, so reservations don't apply; build
  proceeds without them. (Logged in decision log.)
