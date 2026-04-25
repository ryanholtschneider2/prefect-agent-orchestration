# Plan: prefect-orchestration-3cu.3 — `po-slack` tool pack

## Decisions (locked)

- **Pack location**: new sibling repo dir
  `/home/ryan-24/Desktop/Code/personal/nanocorps/po-slack/`, mirroring
  `software-dev/po-formulas/` layout. Importable package name
  `po_slack` (PEP 8 underscores), distribution name `po-slack`.
  Standalone hatchling-built wheel, depends on
  `prefect-orchestration` via local editable path source so
  `po install --editable /…/po-slack` works for dev. No code lands in
  the PO core repo (per principle / issue `pw4`).
- **Slack client**: `slack_sdk` is the **primary code path** for all
  three commands. The official Slack CLI is documented in the skill
  (CLI-first per AC #2) but we don't shell out to it — it's Deno-based
  and overkill for "post a message". Skill content explains both options.
- **Auth model**: `SLACK_BOT_TOKEN` (xoxb-…) is required for post /
  upload / react. `SLACK_APP_TOKEN` (xapp-…) is **optional** and only
  validated by doctor when present (Socket-Mode-only). Doctor never
  red-flags a missing `SLACK_APP_TOKEN`.
- **`slack-upload`** uses `WebClient.files_upload_v2` (the v1
  `files.upload` endpoint is deprecated).
- **Doctor probes `auth.test`** for "workspace reachable", wrapped in
  a 3 s socket timeout (under core's 5 s pack-check timeout). Network
  errors / timeouts → yellow; explicit `invalid_auth` / non-200 →
  red; success → green with `team` + `user` in the message.

## Affected files (all NEW under `/home/ryan-24/Desktop/Code/personal/nanocorps/po-slack/`)

```
po-slack/
├── pyproject.toml                         # hatchling, slack_sdk dep, 4 EP groups
├── README.md                              # short pack overview + install snippet
├── po_slack/
│   ├── __init__.py
│   ├── client.py                          # _make_client() helper (token + timeout)
│   ├── commands.py                        # slack_post / slack_upload / slack_react
│   └── checks.py                          # bot_token_valid / workspace_reachable
├── overlay/
│   └── .claude/
│       └── nanocorp-rules/
│           └── slack.md                   # channel naming, @-mention etiquette,
│                                          # no-DMs-to-clients-without-approval
└── skills/
    └── slack/
        └── SKILL.md                       # CLI-first docs, links to api.slack.com
```

(Final overlay subpath may be `overlay/AGENTS.md` or
`overlay/.claude/nanocorp-rules/slack.md` — chosen during build to match
the convention `software-dev/po-formulas` settles on; see Risks.)

## Approach

### `pyproject.toml` (entry-points)

Mirror `software-dev/po-formulas/pyproject.toml` exactly for layout —
hatchling, `packages = ["po_slack"]`, `[tool.uv.sources]` editable
pointer to `../prefect-orchestration`. Entry-point declarations:

```toml
[project]
name = "po-slack"
dependencies = ["prefect-orchestration", "slack_sdk>=3.27"]

[project.entry-points."po.commands"]
slack-post   = "po_slack.commands:slack_post"
slack-upload = "po_slack.commands:slack_upload"
slack-react  = "po_slack.commands:slack_react"

[project.entry-points."po.doctor_checks"]
slack-bot-token       = "po_slack.checks:bot_token_valid"
slack-workspace-reach = "po_slack.checks:workspace_reachable"
```

No `po.formulas` / `po.deployments` — this is a tool pack, not a flow
pack. (Pack discovery in `prefect_orchestration/pack_overlay.py`
already includes `po.commands` and `po.doctor_checks` in
`PO_ENTRY_POINT_GROUPS`, so overlay + skill materialization will
fire.)

### `po_slack/commands.py`

Three callables. Argument parsing mirrors core's `po <command>`
dispatch contract (`--key value` → `kwarg`). All three share a
`_make_client()` helper that reads `SLACK_BOT_TOKEN` from env, raises
`SystemExit(2)` with a clear message if missing, and constructs a
`slack_sdk.WebClient(token, timeout=10)`.

- **`slack_post(channel: str, text: str, thread_ts: str | None = None) -> None`**
  Calls `client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)`.
  Prints the resulting `ts` so callers can capture for threading. On
  `SlackApiError` print `error: <code>` and `SystemExit(1)`.
- **`slack_upload(channel: str, file: str, title: str | None = None, comment: str | None = None) -> None`**
  Resolves `file` to `Path`, validates it exists, calls
  `client.files_upload_v2(channel=channel, file=str(path), title=title, initial_comment=comment)`.
  Prints uploaded `file.id` + `permalink`.
- **`slack_react(channel: str, ts: str, name: str) -> None`**
  Strips a leading `:` and trailing `:` from `name` so both
  `--name thumbsup` and `--name :thumbsup:` work. Calls
  `client.reactions_add(channel=channel, timestamp=ts, name=name)`.
  Prints `ok`. Treats `already_reacted` as success (idempotent).

All three use type hints, no f-strings in logging, no shadowing.

### `po_slack/checks.py`

Two `DoctorCheck` callables, both wrapping calls in `try/except`
returning yellow on `socket.timeout` / `requests` connection errors,
red on `SlackApiError` with `error == "invalid_auth"` or `not_authed`.

- **`bot_token_valid()`** — Verifies `SLACK_BOT_TOKEN` env var is set
  and matches the `xoxb-` prefix (cheap, no API call). Yellow if
  unset (this pack is opt-in by env), red if set but malformed.
  Also checks `SLACK_APP_TOKEN` if present and warns yellow on bad
  prefix; never red for missing.
- **`workspace_reachable()`** — Skips (yellow `"SLACK_BOT_TOKEN unset"`)
  if no token. Otherwise constructs a `WebClient(token, timeout=3)`
  and calls `auth.test`. Green with `team=<…> user=<…>`. Red on
  `invalid_auth`. Yellow on socket timeout / network error.

### `overlay/` content

Plain Markdown nanocorp-rules content. No client names. Three sections:
**Channel naming** (kebab-case, prefixes like `proj-`, `client-`),
**@-mention etiquette** (don't @channel without explicit need; thread
replies in busy channels), **No DMs to clients without approval**
(route external comms through the project channel; PM-approved DMs
only). Lives at `overlay/AGENTS.md` (preferred — single Claude-Code-
visible file at rig root) so `pack_overlay._copy_tree` lays it down
exactly once and `skip_existing=True` protects an existing rig
`AGENTS.md`. (Fallback: nest under
`overlay/.claude/nanocorp-rules/slack.md` if rigs commonly have an
`AGENTS.md` already — finalized during build by inspecting the
target rig.)

### `skills/slack/SKILL.md`

CLI-first per AC #2: opens with the official Slack CLI link
(`https://api.slack.com/automation/cli/`) and a paragraph on when to
use it. Then "shipped helpers" section documenting `po slack-post`,
`po slack-upload`, `po slack-react` with one example per. Links to
`https://api.slack.com/docs` and notes `slack_sdk` as the underlying
library. Skill frontmatter uses the standard CLAUDE-Code skill schema
(name, description) so `apply_skills()` lands it at
`<rig>/.claude/skills/po-slack/slack/SKILL.md`.

### Install / wiring

No code change in this repo. After the new pack lands, validation is:

```bash
po install --editable /home/ryan-24/Desktop/Code/personal/nanocorps/po-slack
po update
po packs                  # po-slack listed with 3 commands + 2 checks
po list                   # slack-post / slack-upload / slack-react under KIND=command
po show slack-post        # signature + docstring
po doctor                 # two SOURCE=po-slack rows
```

## Acceptance criteria (verbatim)

1. dep: slack_sdk
2. skills/slack/SKILL.md CLI-first with links
3. 3 commands
4. 2 doctor checks
5. overlay

## Verification strategy

| AC | How verified |
|---|---|
| (1) `slack_sdk` dep | `grep -F 'slack_sdk' /…/po-slack/pyproject.toml` shows it in `[project].dependencies`; `uv pip show slack_sdk` after `po install --editable …` confirms install. |
| (2) `skills/slack/SKILL.md` CLI-first with links | File exists at the relative path; `head -40` shows Slack CLI link before `slack_sdk` reference; both `https://api.slack.com/automation/cli/` and `https://api.slack.com/docs` URLs present. After `po install`, `ls /tmp/test-rig/.claude/skills/po-slack/slack/SKILL.md` shows the materialized copy (created by an `AgentSession` start in a throwaway rig, calling `materialize_packs(rig_path, role=None)`). |
| (3) 3 commands | `po list` output contains rows `slack-post`, `slack-upload`, `slack-react`, all `KIND=command`, all `SOURCE=po-slack`. `po show slack-post` prints signature `(channel: str, text: str, thread_ts: str \| None = None)`. |
| (4) 2 doctor checks | `po doctor` table includes `slack-bot-token` and `slack-workspace-reach` rows with `SOURCE=po-slack`. With env unset both are yellow (not red — pack-not-configured ≠ broken). |
| (5) overlay | After `po install --editable …`, `materialize_packs(<test_rig>, role=None)` returns a non-empty list under key `po-slack:overlay`; the file lands at `<test_rig>/AGENTS.md` (or nested path) with the three nanocorp-rule sections. |

## Test plan

- **Unit (in `po-slack/tests/`)**:
  - `test_commands.py`: monkeypatch `slack_sdk.WebClient` with a fake
    that records calls. Verify each command builds the right kwargs
    (`chat_postMessage`, `files_upload_v2`, `reactions_add`); verify
    `:thumbsup:` → `thumbsup` normalization; verify
    `already_reacted` is treated as success; verify `SystemExit(2)`
    when `SLACK_BOT_TOKEN` unset.
  - `test_checks.py`: monkeypatch env + `WebClient.auth_test`. Cover
    six paths: token unset (yellow×2), bad prefix (red), good token
    + auth ok (green), auth `invalid_auth` (red), socket timeout
    (yellow). Use `slack_sdk.errors.SlackApiError` directly — no
    network.
- **Pack-wiring (smoke, run from `prefect-orchestration` repo)**:
  - After `po install --editable ../po-slack` add a minimal
    `tests/e2e/test_po_slack_pack.py` here that:
    1. Asserts `prefect_orchestration.packs.list_packs()` (or the
       backing `discover_packs()` from `pack_overlay`) finds
       `po-slack`.
    2. Runs `subprocess.run(["po", "list"], …)` and greps stdout for
       the three command names.
    3. Runs `subprocess.run(["po", "doctor"], …)` and asserts both
       check names appear with `SOURCE=po-slack`.
  - This is the bridge that proves the pack actually wires into core.
- **Playwright**: N/A (no UI).
- **e2e against real Slack**: out of scope. Documented in the skill
  as "set `SLACK_BOT_TOKEN` and run `po slack-post --channel
  #ryan_claude_code --text 'hello'` to live-test."

## Risks

- **Pack lives in a separate repo** — neither this run nor PO's git
  hooks can `git add` files outside `--rig-path`. The builder will
  need to `cd /home/ryan-24/Desktop/Code/personal/nanocorps/po-slack`
  for any `git add`/`commit` if that dir is itself a git repo, or
  initialize one (`git init`) if not. Note in the build prompt: do
  NOT stage the new pack files inside `prefect-orchestration`.
- **Test file location** — adding
  `tests/e2e/test_po_slack_pack.py` inside the PO core repo creates
  a soft cross-repo coupling (the test fails if `po-slack` isn't
  installed). Mitigation: gate with
  `pytest.importorskip("slack_sdk")` + a `pytest.skip(...)` if
  `po-slack` isn't in `discover_packs()`. Keeps CI green when the
  sibling pack isn't checked out.
- **`slack_sdk` API drift** — `files_upload_v2` is current as of
  `slack_sdk==3.27`; pin `>=3.27`. Older versions only have
  deprecated `files.upload`.
- **Overlay file naming convention** — placing content at
  `overlay/AGENTS.md` will silently merge into a rig that already
  has one (skip-existing semantics in `_copy_tree`). If the rig
  needs the slack rules visible to Claude even when an `AGENTS.md`
  exists, nest under `overlay/.claude/nanocorp-rules/slack.md`
  instead. Builder picks based on what's already in the test rig.
- **Doctor check `auth.test` requires network** in CI — kept hermetic
  by yellow-on-timeout (no red, no test failure). The unit test
  monkeypatches the client; the live call only happens when an
  operator runs `po doctor`.
- **No API contract change** in `prefect-orchestration` core — this
  pack is purely additive via existing entry-point groups. No
  migrations. No breaking consumers.
- **Baseline failures** (6 pre-existing failing tests in
  `tests/test_agent_session_tmux.py`, `tests/test_mail.py`,
  `tests/test_watch.py`, `tests/e2e/test_po_deploy_cli.py`) are
  unrelated to this pack and out of scope; the regression-gate
  should compare against baseline, not against zero failures.
