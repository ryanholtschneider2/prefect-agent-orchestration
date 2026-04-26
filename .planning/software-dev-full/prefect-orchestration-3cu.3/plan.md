# Plan: prefect-orchestration-3cu.3 — `po-slack` tool pack

## Current state (re-entry)

The pack already exists at
`/home/ryan-24/Desktop/Code/personal/nanocorps/po-slack/` (sibling repo,
its own `.git`, editable-installed — visible in `po packs` as
`po-slack 0.1.0` contributing `commands=slack-post,slack-react,slack-upload`
and `doctor_checks=slack-bot-token,slack-workspace-reach`). All five ACs
appear met on disk; this plan documents that, identifies gaps, and
defines the verification harness so the critic / verifier can confirm
without re-implementing.

## Decisions (locked, carried from prior iteration)

- **Pack location**: `/home/ryan-24/Desktop/Code/personal/nanocorps/po-slack/`
  (sibling repo). NO code lands inside `prefect-orchestration/`. The
  pack is its own git repo with its own `pyproject.toml` /
  `uv.lock` / `tests/`.
- **Slack client**: `slack_sdk>=3.27` is the **only** runtime path
  for the three commands. Official Slack CLI is referenced in the
  skill (CLI-first per AC 2) but never shelled out to.
- **Auth model**: `SLACK_BOT_TOKEN` (`xoxb-…`) drives the three
  commands. `SLACK_APP_TOKEN` is optional / Socket-Mode-only and
  never red-flagged when missing.
- **`slack-upload`** uses `WebClient.files_upload_v2`.
- **Doctor `workspace_reachable`** wraps `auth.test` with a 3 s
  timeout; network errors → yellow, `invalid_auth` → red, success
  → green.
- **Overlay path**: `overlay/nanocorp-rules/slack.md` (NOT
  `overlay/AGENTS.md`) so it always materializes without colliding
  with a rig-level `AGENTS.md`. Already in place.
- **`po-slack` is opt-in by env**: doctor checks return yellow
  (not red) when `SLACK_BOT_TOKEN` is unset — installing the pack
  does not break a rig that has no Slack creds.

## Affected files (all under `/home/ryan-24/Desktop/Code/personal/nanocorps/po-slack/`)

```
po-slack/
├── pyproject.toml                        # ✓ exists — slack_sdk>=3.27, 5 EPs
├── README.md                             # ✓ exists
├── po_slack/
│   ├── __init__.py                       # ✓
│   ├── client.py                         # ✓ _make_client()
│   ├── commands.py                       # ✓ slack_post / slack_upload / slack_react
│   └── checks.py                         # ✓ bot_token_valid / workspace_reachable
├── overlay/
│   └── nanocorp-rules/
│       └── slack.md                      # ✓ exists
├── skills/
│   └── slack/
│       └── SKILL.md                      # ✓ exists, CLI-first, links present
└── tests/
    ├── test_commands.py                  # ✓ 8 tests
    └── test_checks.py                    # ✓ 11 tests, all passing
```

Only edits anticipated this iteration: small content fixes if the
critic flags gaps in any of the four "shipped" assets above. No new
files in `prefect-orchestration/`.

## Approach

### What's already correct (do not touch)

1. **`pyproject.toml`** declares `slack_sdk>=3.27` (AC 1) and the
   five entry points across `po.commands` and `po.doctor_checks`.
2. **`po_slack/commands.py`** ships exactly three callables
   (`slack_post`, `slack_upload`, `slack_react`) backed by
   `slack_sdk.WebClient`, with `:emoji:` normalization,
   `already_reacted` idempotency, and `SystemExit(2)` on missing
   `SLACK_BOT_TOKEN`.
3. **`po_slack/checks.py`** ships exactly two `DoctorCheck`-returning
   callables, both yellow-on-missing (so `po doctor` stays green for
   rigs without Slack configured).
4. **`skills/slack/SKILL.md`** opens with the Slack CLI link and the
   "two paths" framing, lists all three `po slack-*` examples, and
   links `https://api.slack.com/docs` plus `slack_sdk` Python docs.
5. **`overlay/nanocorp-rules/slack.md`** documents channel naming,
   @-mention etiquette, and no-client-DMs-without-approval. Materializes
   into `<rig>/nanocorp-rules/slack.md` via core's pack-overlay copier.
6. **`tests/test_commands.py` + `tests/test_checks.py`** — 19 tests,
   currently 100% passing under `uv run python -m pytest` from the
   pack root.

### What this iteration does

The pack is functionally complete; this iteration's deliverables are:

1. **Verification harness**: confirm each AC against the live
   `po install --editable …` state on this machine and write the
   evidence into the run dir's verdict files. No new code unless a
   critic finds a gap.
2. **Gap-fix only if critic flags it**. Possible gaps the critic may
   raise (and the targeted patch each would warrant):
   - SKILL.md missing a CLI link → add the missing URL.
   - Overlay missing one of the three nanocorp sections → append the
     section.
   - `po doctor` showing red for an unset `SLACK_BOT_TOKEN` → adjust
     `bot_token_valid()` to return yellow.
   - `slack_sdk` version pin too loose / too tight → adjust
     `pyproject.toml`, regenerate `uv.lock`, reinstall.
3. **Do NOT** add tests inside `prefect-orchestration/` that depend
   on `po-slack` being installed (would create a soft cross-repo
   coupling and would need `pytest.importorskip` gates). Pack-level
   tests already live in the pack repo.

### Out of scope (do not address this iteration)

- The 7 baseline failures in `prefect-orchestration/tests/`
  (`test_agent_session_tmux.py`, `test_mail.py`, `test_watch.py`,
  `test_deployments.py::test_po_list_still_works`,
  `tests/e2e/test_po_deploy_cli.py`) are **pre-existing** (visible
  in `baseline.txt` captured BEFORE this run started — though
  `test_po_list_still_works` may now reflect the presence of
  installed packs). They belong to other beads, not to 3cu.3. The
  regression-gate must compare against baseline, not against zero
  failures, per the rig's standing protocol.
- No changes to `prefect-orchestration/` core. Per principle "land
  pack-contrib code in the pack's repo, not in core" (issue `pw4`),
  every code path 3cu.3 needs is already in the pack repo.

## Acceptance criteria (verbatim)

1. dep: slack_sdk
2. skills/slack/SKILL.md CLI-first with links
3. 3 commands
4. 2 doctor checks
5. overlay

## Verification strategy

| AC | Concrete check (run from `prefect-orchestration` rig) |
|---|---|
| (1) `slack_sdk` dep | `grep -E '^\s*"slack_sdk' /home/ryan-24/Desktop/Code/personal/nanocorps/po-slack/pyproject.toml` returns the line `"slack_sdk>=3.27"`; `uv pip show slack_sdk --python /…/po-slack/.venv/bin/python` returns ≥3.27. |
| (2) `skills/slack/SKILL.md` CLI-first | File exists at `/…/po-slack/skills/slack/SKILL.md`. `head -50` shows the Slack CLI URL (`https://api.slack.com/automation/cli/`) appears within the first ~40 lines (CLI-first framing). `grep -F 'https://api.slack.com/docs' …/SKILL.md` and `grep -F 'slack.dev/python-slack-sdk' …/SKILL.md` both succeed. |
| (3) 3 commands | `po list` (run from a beads-initialized rig) prints rows for `slack-post`, `slack-upload`, `slack-react` with `KIND=command` and `MODULE:CALLABLE` of `po_slack.commands:slack_*`. `po show slack-post` prints the signature and docstring. |
| (4) 2 doctor checks | `po doctor` (in a rig where `SLACK_BOT_TOKEN` is unset) shows two extra rows from this pack: `slack-bot-token` and `slack-workspace-reach`, both yellow (warn), neither red. With a deliberately bogus `SLACK_BOT_TOKEN=xoxb-fake`, `slack-workspace-reach` flips red on `invalid_auth`. |
| (5) overlay | `/home/ryan-24/Desktop/Code/personal/nanocorps/po-slack/overlay/nanocorp-rules/slack.md` exists and contains the three sections: channel naming, @-mention etiquette, no-client-DMs-without-approval. After a fresh `materialize_packs(<tmp_rig>, role=None)` call (or starting an `AgentSession` against a tmp rig with `apply_overlays=True`), the file lands at `<tmp_rig>/nanocorp-rules/slack.md`. |

## Test plan

- **Unit (in `po-slack/tests/`, already passing)** — `uv run python -m pytest`
  from the pack root. 19 tests cover: command kwarg construction,
  `:emoji:` normalization, `already_reacted` idempotency, missing-token
  `SystemExit(2)`, doctor token-prefix validation, doctor `auth.test`
  green/yellow/red paths.
- **Pack-wiring smoke (manual / verifier)** — from
  `prefect-orchestration` rig:
  - `po packs` includes the `po-slack` row.
  - `po list | grep -E '^command\s+slack-'` returns three lines.
  - `po doctor | grep po-slack` returns two lines.
  - `po show slack-upload` returns a non-empty signature/docstring.
- **No e2e against the live Slack API** in this iteration. The skill
  documents the manual smoke (`po slack-post --channel
  #ryan_claude_code --text 'hello'`).
- **Playwright** — N/A (no UI).

## Risks

- **Cross-repo `git add`**: builder must `cd /…/po-slack` before
  staging/committing any changes inside the pack — `prefect-orchestration`
  hooks will refuse files outside its tree. Decision-log must record
  pack commits separately from core (none expected this iteration).
- **`po install` cache vs editable mode**: if a critic-driven edit
  changes `pyproject.toml` entry points, builder must re-run `po update`
  so `importlib.metadata` re-reads EPs (per CLAUDE.md guidance).
- **Doctor liveness**: `workspace_reachable` calls `auth.test` over
  the network when a real `SLACK_BOT_TOKEN` is set. The 3 s timeout
  and yellow-on-network-error semantics keep `po doctor` from
  hanging in offline / CI environments.
- **No API contract changes** in core; pack is purely additive via
  existing `po.commands` / `po.doctor_checks` entry-point groups.
  No migrations. No breaking consumers.
- **Pre-existing baseline failures** are unrelated to 3cu.3 and out
  of scope; regression-gate compares against baseline.
