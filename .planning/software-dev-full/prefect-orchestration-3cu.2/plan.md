# Plan: prefect-orchestration-3cu.2 — `po-gcal` tool pack

## Scope

Build a new sibling pack `po-gcal` (mirroring the layout of
`../software-dev/po-formulas/`) that contributes Google Calendar tooling
to PO-managed agents. The pack ships:

- 3 `po.commands` (utility ops dispatched as `po <command>`, *not*
  `po run`) — `gcal-today`, `gcal-create`, `gcal-free`.
- 2 `po.doctor_checks` — credential-presence + reachability.
- A `skills/gcal/SKILL.md` (CLI-first agent doc).
- An `overlay/CLAUDE.md` snippet that is auto-merged into the rig
  cwd by core's pack_overlay machinery (already shipped via 4ja.4).
- A `pyproject.toml` declaring `google-api-python-client` as a runtime
  dep and registering all of the above via entry points.

This work lands in **`/home/ryan-24/Desktop/Code/personal/nanocorps/po-gcal/`** —
a new sibling to `prefect-orchestration/` and `software-dev/`. **Nothing
in `prefect-orchestration/` itself should change** — this issue is pure
pack authoring against shipped extension points (`po.commands`,
`po.doctor_checks`, `overlay/`, `skills/`).

## Affected files (new)

```
../po-gcal/
├── pyproject.toml
├── README.md
├── po_gcal/
│   ├── __init__.py
│   ├── commands.py        # gcal_today, gcal_create, gcal_free
│   ├── checks.py          # creds_present, calendar_reachable
│   └── _client.py         # CLI-vs-SDK selection, auth resolution
├── overlay/
│   └── CLAUDE.md          # snippet auto-copied into rig cwd
├── skills/
│   └── gcal/
│       └── SKILL.md       # CLI-first agent doc
└── tests/
    ├── test_commands.py
    └── test_checks.py
```

No edits anywhere under `prefect-orchestration/`.

## Approach

### Pack scaffolding

Mirror `software-dev/po-formulas/pyproject.toml` literally:

```toml
[project]
name = "po-gcal"
version = "0.1.0"
description = "po pack: Google Calendar tooling (today / create / free-busy) + doctor checks + agent skill"
requires-python = ">=3.11"
dependencies = [
    "prefect-orchestration",
    "google-api-python-client>=2.0",
    "google-auth>=2.0",
    "google-auth-oauthlib>=1.0",
]

[project.entry-points."po.commands"]
gcal-today  = "po_gcal.commands:gcal_today"
gcal-create = "po_gcal.commands:gcal_create"
gcal-free   = "po_gcal.commands:gcal_free"

[project.entry-points."po.doctor_checks"]
gcal-creds-present  = "po_gcal.checks:creds_present"
gcal-reachable      = "po_gcal.checks:calendar_reachable"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["po_gcal", "overlay", "skills"]
```

`overlay/` and `skills/` are listed in the wheel-packages list so the
trees survive `pip install` and core's `pack_overlay.py` finds them at
the *dist root* (it probes both `<pack>/overlay/` and
`<pack>/<module>/overlay/`).

### Auth resolution (`_client.py`)

Single source-of-truth for "where do calendar creds come from", used by
both commands and checks. Resolution order:

1. `GOOGLE_APPLICATION_CREDENTIALS` env var → service-account JSON path.
   `google.oauth2.service_account.Credentials.from_service_account_file`.
2. ADC via `google.auth.default(scopes=[...])` (covers `gcloud
   auth application-default login`).
3. `~/.config/gcloud/application_default_credentials.json` direct read
   as a last resort.
4. None → checks return `red`; commands `SystemExit(2)` with a clear
   "set GOOGLE_APPLICATION_CREDENTIALS or run `gcloud auth
   application-default login`" hint.

Scope: `https://www.googleapis.com/auth/calendar`.

CLI-vs-SDK: triage notes `gcloud` has no calendar verbs. Decision:
**SDK is primary** (`googleapiclient.discovery.build("calendar", "v3",
credentials=...)`). The "CLI-first" framing stays in the *agent skill*
(`SKILL.md`) — agents are told to run `po gcal-*` commands as their
CLI, regardless of how the Python under the hood reaches Google.

### Commands (`commands.py`)

All three signatures take string-coerced kwargs (per `po`'s arg parser
in `commands.py:_parse_argv`) and print to stdout. Output is JSON for
machine-readability (matches `summarize_verdicts`-style discipline of
clear, line-based output).

- **`gcal_today(calendar: str = "primary", json_out: bool = True) -> None`**
  - Uses `events().list(calendarId=calendar, timeMin=<today 00:00 local>,
    timeMax=<tomorrow 00:00 local>, singleEvents=True, orderBy="startTime")`.
  - Prints JSON list of `{id, summary, start, end, attendees}`.
  - On no creds → `SystemExit(2)` with hint.

- **`gcal_create(calendar: str = "primary") -> None`**
  - Reads JSON from `sys.stdin` — schema follows Google's `Event`
    resource (`summary`, `start`, `end`, `attendees`, …). Triage left
    the input format unspecified; **JSON-on-stdin** is the cleanest
    choice and matches how agents will compose requests.
  - Posts via `events().insert(calendarId=calendar, body=payload)`.
  - Prints created event's `{id, htmlLink}`.

- **`gcal_free(user: str, start: str, end: str, calendar: str = "primary") -> None`**
  - `start`/`end` accept ISO-8601; default `end = start + 1h` if absent
    is rejected (require both for predictability).
  - Calls `freebusy().query(body={timeMin, timeMax, items=[{id: user}]})`.
  - Prints JSON `{user, busy: [{start, end}, ...]}`.

All three wrap API errors (`googleapiclient.errors.HttpError`,
`google.auth.exceptions.GoogleAuthError`) into `SystemExit(2)` with a
one-line stderr message — never bubble a full traceback to an agent
caller.

### Doctor checks (`checks.py`)

- **`creds_present() -> DoctorCheck`** — runs the auth-resolution chain
  (no API call); green if any credential source resolves, red with
  hints listing all three options if none, yellow if the file exists
  but fails to load.

- **`calendar_reachable() -> DoctorCheck`** — uses resolved creds (if
  any) to do a single cheap `calendarList().list(maxResults=1).execute()`
  with a 4-second timeout. Green on success, yellow on timeout, red on
  HTTP / auth error. If `creds_present()` is red, this short-circuits
  yellow with "skipped: no creds" so the table reads cleanly.

Pattern lifted directly from `po_formulas/checks.py:claude_cli_present`.

### `overlay/CLAUDE.md`

Short snippet (≤ 40 lines) appended into the rig cwd at session start by
core's `pack_overlay.apply_overlay`. Contents:

- "When you need to read or write Ryan's Google Calendar, prefer
  `po gcal-today`, `po gcal-create`, `po gcal-free`. See
  `.claude/skills/po-gcal/gcal/SKILL.md`."
- One-line auth hint: "If `po doctor` shows gcal red, run `gcloud auth
  application-default login`."
- Marker comments (`<!-- po-gcal:begin -->` / `<!-- po-gcal:end -->`) so
  re-applies are idempotent (matches existing overlay convention if
  any; otherwise just append).

### `skills/gcal/SKILL.md`

CLI-first agent doc. Sections:

- **When to invoke** — "the user mentions calendar, free time, meeting".
- **Commands** — verbatim signatures + one example invocation each
  (today / create-from-stdin / free-busy).
- **Auth troubleshooting** — pointer to `po doctor`.
- **Reference docs** — links to:
  - https://developers.google.com/calendar/api/v3/reference
  - https://developers.google.com/calendar/api/quickstart/python
  - any `llms.txt` if Google ships one (optional — best-effort link).

Triage requires "links to calendar API docs + any llms.txt"; the above
satisfies that.

### Tests (`tests/`)

- **`test_commands.py`**:
  - Argument parsing — invoke each command with `--help`-style probing
    via `commands.run_command(name, [...])` (or directly call the
    callable) and assert it errors cleanly when creds absent.
  - Stub the Google client by monkey-patching `_client.build_service`
    to return a fake exposing `events()` / `freebusy()` / etc. Verify
    each command formats JSON output correctly and exits 0.
- **`test_checks.py`**:
  - `creds_present` returns red when env unset + ADC absent (use
    `monkeypatch.setenv` + `tmp_path` for `HOME`).
  - `creds_present` returns green when `GOOGLE_APPLICATION_CREDENTIALS`
    points at a valid JSON file (write a fake key).
  - `calendar_reachable` short-circuits yellow when creds_present is
    red.
  - No live API calls — `googleapiclient.discovery.build` is patched.

Live-API tests are gated behind `GCAL_E2E=1` env var and skipped by
default (matches the project's "no live calls in unit tests" norm).

## Acceptance criteria (verbatim from issue)

> (1) dep: google-api-python-client; (2) skills/gcal/SKILL.md CLI-first
> with doc links; (3) 3 commands; (4) 2 doctor checks; (5)
> overlay/CLAUDE.md.

## Verification strategy

| AC | How verified |
|---|---|
| (1) dep `google-api-python-client` | `grep '^google-api-python-client' po-gcal/pyproject.toml`; `uv pip install -e ../po-gcal && uv pip show google-api-python-client` succeeds. |
| (2) `skills/gcal/SKILL.md` CLI-first w/ doc links | File exists at `po-gcal/skills/gcal/SKILL.md`; first non-frontmatter section is "Commands" not "Library API"; `grep -E 'developers\.google\.com/calendar' SKILL.md` returns ≥1 hit. |
| (3) 3 commands | After `po install --editable ../po-gcal && po update`, `po list` shows `gcal-today`, `gcal-create`, `gcal-free` with `KIND=command`. `po show gcal-today` succeeds. Each is callable: `echo '{}' \| po gcal-create` exits non-zero with credential hint (proving wiring without needing live creds). |
| (4) 2 doctor checks | `po doctor` table contains rows `gcal creds present` and `gcal calendar reachable` with `SOURCE=po-gcal`. With env unset, first is red; with a valid `GOOGLE_APPLICATION_CREDENTIALS`, both green (live test, gated). |
| (5) `overlay/CLAUDE.md` | After installing the pack, copy a temp rig and run a stub flow; verify `pack_overlay.apply_overlay` materialized `<rig>/CLAUDE.md` (or appended a marked block). Existing core test infra around `pack_overlay` provides the pattern; we'll add one pack-local unit test calling `apply_overlay` directly with a temp dest. |

## Test plan

- **Unit (pack-local pytest)** — primary layer: command output
  formatting, doctor-check status logic, auth resolution chain. Mocks
  out Google SDK at `googleapiclient.discovery.build`. Run via
  `cd ../po-gcal && uv run python -m pytest`.
- **e2e (manual)** — `po install --editable`, `po update`, `po list`,
  `po doctor`, `po show gcal-today` round-trip from the
  `prefect-orchestration` rig. Captured as a one-line shell snippet in
  the pack README.
- **Playwright** — N/A (no UI; `has_ui=false`).
- **Live API** — skipped by default; one optional smoke under
  `GCAL_E2E=1` that hits a test calendar.

## Risks

- **Auth path**: agents running headless can't complete an interactive
  OAuth flow. Mitigation: support service-account JSON + ADC only;
  document the `gcloud auth application-default login` one-time setup
  in `SKILL.md` and `overlay/CLAUDE.md`.
- **Wheel packaging of `overlay/` and `skills/`**: hatchling needs the
  trees explicitly listed in `tool.hatch.build.targets.wheel.packages`
  *and* every dir must contain at least one file (so the wheel
  preserves it). Add `__init__.py`-equivalents (e.g. an empty
  `.gitkeep`) defensively. Verify the built wheel ships them with
  `python -m zipfile -l dist/*.whl | grep -E '(overlay|skills)/'`.
- **`po install` collision detection** (4ja.1): the new commands must
  not shadow core verbs. Names `gcal-*` are safe (no core verb starts
  with `gcal`); `po install` will surface any future collision loudly.
- **Pack-location convention**: triage flags this — code lands in a new
  sibling repo, *not* in `prefect-orchestration/`. Builder must `cd`
  into `../po-gcal/` for git operations (per polyrepo guidance in
  global CLAUDE.md). No git remote yet — local commits only.
- **API contract**: no consumers exist yet; safe to iterate freely on
  command flag names.
- **Migrations**: none.
