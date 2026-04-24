# CLAUDE.md — `prefect-orchestration` / `po`

Guide for Claude Code agents (and humans) working in this repo or any
repo where `po` is installed. Load this when a task involves `po`, a
formula pack, a scheduled PO deployment, or the interaction between
beads, PO flows, and Prefect.

> **Agents:** the canonical "how to use `po`" reference is shipped with
> this repo at [`skills/po/SKILL.md`](skills/po/SKILL.md) — load it
> whenever the user asks to dispatch a beads issue/epic, mentions the
> `po` CLI, or wants to run an actor-critic pipeline. Other repos pick
> it up via `~/.claude/skills/po/` (symlink or copy of that file).

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**This repo has no git remote configured** — local-only. The beads
integration block below references `git push` / `bd dolt push`; skip those
steps until a remote exists. Still: close finished beads, commit code,
leave a handoff note.
<!-- END BEADS INTEGRATION -->

## Working on this repo

This repo is the **core** (`prefect-orchestration` package + `po` CLI).
Formulas live in sibling packs (e.g. `../software-dev/po-formulas/`).

```bash
uv sync                                     # install + dev deps
uv run python -m pytest                     # full test suite (unit + e2e)
uv run python -m pytest tests/test_status.py        # one file
uv run python -m pytest tests/test_status.py::test_name  # one test
uv run python -m pytest tests/e2e/          # only e2e (CLI roundtrips)
uv run python -m pytest -k "deploy"         # by keyword

# Re-install after editing pyproject.toml entry points (metadata is
# baked at install time, not on code reload):
uv tool install --force \
  --editable . \
  --with-editable ../../../software-dev/po-formulas
```

`tests/` is split into unit tests (one file per module under `prefect_orchestration/`)
and `tests/e2e/` (subprocess-driven `po` CLI roundtrips). E2E tests
shell out to `po` and `bd`, so both must be on PATH; many also need
a Prefect server reachable at `PREFECT_API_URL`.

### Core module map

| Module | Role |
|---|---|
| `cli.py` | Typer entry point; discovers `po.formulas` + `po.deployments` entry points; subcommands `list`/`show`/`run`/`logs`/`artifacts`/`sessions`/`watch`/`retry`/`status`/`deploy`/`doctor`. |
| `agent_session.py` | `AgentSession` + `SessionBackend` Protocol (`ClaudeCliBackend`, `TmuxClaudeBackend`, `StubBackend`). Per-role `--resume <uuid>` + `--fork-session`. |
| `beads_meta.py` | `MetadataStore` Protocol; `BeadsStore` (shells `bd`) + `FileStore` (JSON fallback); `claim_issue`/`close_issue`/`list_epic_children`. |
| `parsing.py` | `read_verdict()` — reads `$RUN_DIR/verdicts/<step>.json`. |
| `templates.py` | `{{var}}` substitution over a caller-supplied prompts dir. |
| `artifacts.py`, `sessions.py`, `watch.py`, `retry.py`, `status.py`, `run_lookup.py`, `doctor.py`, `deployments.py` | Back the matching `po` subcommand. |

## What PO is

`po` is the **CLI + Python core** for running Prefect-based multi-agent
workflows driven by the Claude Code CLI. It ships no formulas of its own
— every pipeline (software-dev-full, epic, future bio-experiment,
microcorp, …) is a separate installable Python package that registers
itself via **entry points**.

Three-legged stool:

- **Beads** (`bd`) — source of truth for *what* to do. Every unit of
  work is a bead; dependencies are edges.
- **PO flows** — *how* to do it. Python `@flow`s in packs, composed of
  `@task` steps (one per role: triager, builder, critic, verifier, …).
  Verdicts flow between steps via file artifacts (`$RUN_DIR/verdicts/<step>.json`),
  not LLM-JSON parsing.
- **Prefect** — *when* and *where*. DAG scheduler, UI, retries, work
  pools, concurrency limits. Beads-deps compile to Prefect `wait_for=`.

## Principles

See [`engdocs/principles.md`](engdocs/principles.md). Load before
adding a `po` verb or deciding between a `po` and a `prefect` command.

Short version: PO wraps things Prefect doesn't know about (entry
points, rig-path, run-dir, per-role session UUIDs); **CLI is the
primary surface**; defer to `prefect` for anything pure-Prefect.

## Common workflows

### Inspecting what's available

```bash
po list                     # every formula registered by every installed pack
po show <formula>           # signature + docstring for one formula
po deploy                   # pack-declared deployments (not yet applied)
po doctor                   # wiring health check (bd, Prefect, pools, entry points)
prefect deployment ls       # deployments currently on the server
```

### Running a beads issue end-to-end

```bash
# 1. Find and claim work
bd ready
bd show <issue-id>

# 2. Run the actor-critic pipeline on it
po run software-dev-full \
  --issue-id <issue-id> \
  --rig <name> \
  --rig-path <absolute path to the repo where code lives>

# The flow claims the bead (bd update --claim) on entry, closes it
# (bd close) on successful exit. Don't `bd update` manually during a run.
```

### Running an epic (DAG fan-out)

```bash
po run epic \
  --epic-id <epic-id> \
  --rig <name> \
  --rig-path <path>

# Optional: --max-issues N  to process only the first N topo-sorted children
# Optional: --dry-run       to exercise the DAG without spawning Claude
```

### Scheduling (cron, interval, manual)

```bash
# 1. Ship a register() in your pack's po_formulas/deployments.py
#    (declare the Prefect deployment Pythonically, no YAML)

# 2. Apply to the Prefect server
po deploy --apply

# 3. Start a worker so scheduled runs execute
prefect worker start --pool po        # create with --type process/k8s/docker

# 4. Trigger manual runs with a delay (Prefect-native)
prefect deployment run <flow>/<deployment-name> \
  --param issue_id=<id> --param rig=<rig> --param rig_path=<path> \
  --start-in 2h
```

### Unread-mail auto-injection (AgentSession.prompt)

`AgentSession.prompt()` programmatically prepends any unread mail
addressed to the agent's role as an `<mail-inbox>` XML block before
sending the turn. On successful turn return, the snapshot's messages
are marked read; on exception they remain unread for the next turn.
Empty inbox renders no block.

Wiring is opt-in via two callable fields on `AgentSession`:

- `mail_fetcher: Callable[[str], list]` — `(role) -> list[Mail-like]`
  (the pack wires `po_formulas.mail.inbox`)
- `mail_marker:  Callable[[str], None]` — `(mail_id) -> None`
  (the pack wires `po_formulas.mail.mark_read`)
- `skip_mail_inject: bool = False` — set True for stub/dry-run paths
  to skip the `bd list` shell-out

Core never imports `po_formulas.mail` — keeps the layering clean
(core works without the pack installed). Inbox is capped at
`MAX_INBOX_MESSAGES = 20` most-recent entries to bound prompt size.
Mail arriving mid-turn is not auto-marked — only IDs in the
fetched-at-entry snapshot are closed.

### Backend selection

`software-dev-full` picks an agent-runtime backend automatically:

| Condition | Backend |
|---|---|
| `tmux` on `PATH` (default) | `TmuxClaudeBackend` — each role spawns a named tmux session `po-<issue>-<role>` that you can `tmux attach -t …` mid-turn to watch live |
| `tmux` absent | `ClaudeCliBackend` — subprocess pipes, no lurking |
| `--dry-run` flag | `StubBackend` — no Claude calls, fakes verdict files |

Override with `PO_BACKEND=cli|tmux|stub` on any `po run` invocation.
`PO_BACKEND=tmux` errors loudly if tmux is missing (refuses silent
fallback when you've explicitly asked for it).

Issue IDs with dots (`4ja.1`) are sanitized to `4ja_1` in session
names because tmux treats `.` as a pane separator.

### Concurrency (per-role caps)

```bash
prefect work-pool create po --type process --concurrency-limit 4
prefect concurrency-limit create critic 2
prefect concurrency-limit create builder 3
# Tasks are already tagged with role names in the software-dev pack.
```

### Debugging a run

Every `software-dev-full` run leaves a full paper trail at
`<rig_path>/.planning/software-dev-full/<issue_id>/`:

- `triage.md`, `plan.md`, `build-iter-N.diff`, `critique-iter-N.md`,
  `verification-report-iter-N.md`, `decision-log.md`, `lessons-learned.md`
- `verdicts/<step>.json` — orchestrator-readable pass/fail artifacts
- `metadata.json` — per-role Claude `--resume <uuid>` session ids
- `review-artifacts/` — screenshots, smoke output, etc.

For live runs: `tail -f /tmp/prefect-orchestration-runs/<run>.log` and
the Prefect UI at `http://127.0.0.1:4200` (after `prefect server start`).

`po logs <issue-id>` resolves the run dir via bead metadata
(`po.rig_path` / `po.run_dir`, set at flow entry) and tails the freshest
log/artifact. `-f` streams (`tail -F`), `-n N` overrides tail length,
`--file NAME` picks a specific file in the run dir.

`po artifacts <issue-id>` dumps the whole forensic trail in one scroll:
`triage.md`, `plan.md`, each `critique-iter-N.md` + `verification-report-iter-N.md`
(sorted numerically), `decision-log.md`, `lessons-learned.md`, then every
`verdicts/*.json` pretty-printed. Missing files render as `(missing)` — never
aborts. `--verdicts` prints only JSON verdicts; `--open` launches `$EDITOR`
(TTY) or `xdg-open` on the run dir. ANSI color auto-disables when piped.

`po sessions <issue-id>` reads `metadata.json` at the run dir and prints a
`role | uuid | last-iter | last-updated` table. `--resume <role>` emits a
ready-to-run `claude --print --resume <uuid> --fork-session` one-liner so
you can pick up a role's session outside the flow.

`po watch <issue-id>` merges two live streams into one terminal: Prefect
flow-run state transitions (polled via the client) and new/modified files
appearing in the run_dir (polled via mtime; `watchdog` used if installed).
Lines are prefixed `[prefect]` / `[run-dir]` and timestamped. `--replay`
dumps existing artifacts + the last N flow state transitions before a
`===== live =====` separator; `--replay-n N` tunes N (default 10). If the
flow is already terminal or the Prefect server is unreachable, the run_dir
watcher still streams. Ctrl-C exits 0 cleanly.

`po retry <issue-id>` archives the run_dir to a `.bak-<utc>` sibling under
an advisory lock, reopens the bead if closed, and invokes the formula
in-process. Refuses when a `Running` flow-run for the issue already exists
(pass `--force` to bypass). `--keep-sessions` preserves the prior
`metadata.json` so per-role Claude session UUIDs survive the archive.
`--rig NAME` overrides the default rig (rig_path basename); `--formula NAME`
picks a non-default entry-point.

## When to use `po` vs `prefect`

| Task | Use |
|---|---|
| List installed formulas | `po list` |
| Show a formula's signature / docstring | `po show <formula>` |
| Run a formula synchronously, now | `po run <formula> --args` |
| Tail / follow logs for an issue's run | `po logs <issue-id> [-f] [-n N] [--file NAME]` |
| Dump full forensic trail for a run | `po artifacts <issue-id> [--verdicts] [--open]` |
| Show per-role Claude session UUIDs for a run | `po sessions <issue-id> [--resume <role>]` |
| Archive a run_dir and relaunch its formula | `po retry <issue-id> [--keep-sessions] [--force] [--rig NAME] [--formula NAME]` |
| Live merged feed of flow state + run_dir artifacts | `po watch <issue-id> [--replay] [--replay-n N]` |
| List active / recent runs grouped by bead `issue_id` tag | `po status [--issue-id ID] [--since 24h] [--state Running] [--all]` |
| List pack-declared deployments | `po deploy` |
| Apply pack-declared deployments to server | `po deploy --apply` |
| Check PO wiring (bd, Prefect API, pool, entry points) | `po doctor` |
| List deployments currently on server | `prefect deployment ls` |
| Trigger a deployment (now or future) | `prefect deployment run <name> --start-in 2h` |
| Start server / worker | `prefect server start`, `prefect worker start --pool po` |
| Work-pool / concurrency-limit config | `prefect work-pool …`, `prefect concurrency-limit …` |
| List / cancel flow runs | `prefect flow-run ls`, `prefect flow-run cancel` |

If something you want is only in Prefect's Python API (not its CLI),
that's a candidate for a `po` verb — see principles §2 (CLI first,
Python second).

## When a task requires writing code here

- **Do NOT** add a `po` verb that just wraps a `prefect` subcommand with
  no added context. Pass-through wrappers violate principle §1.
- **Do NOT** parse LLM output to extract step results. Agents write
  verdict files at `$RUN_DIR/verdicts/<step>.json`; the flow reads them.
- **Do NOT** hardcode role names or rig names in core. Roles are
  per-formula; rigs are caller-supplied.
- **Do NOT** create new PO-specific runtime-state directories. On-disk
  state lives in `<rig_path>/.planning/<formula>/<issue>/` (per-run)
  and Prefect's server DB (server-side). Beads owns work state.
- **Do NOT** `git add -A` inside a flow step if other workers may be
  active in the rig concurrently. Build/lint/ralph prompts warn agents
  to use scoped `git add <path>`; if you touch those prompts, preserve
  that guidance.
- **Do** use the `po.formulas` entry-point group for any new flow.
- **Do** use the `po.deployments` entry-point group for any new
  deployment (`register()` returning `RunnerDeployment`s).
- **Do** land pack-contrib code in the pack's repo
  (`../software-dev/po-formulas/po_formulas/`), not in the caller's
  rig-path — see issue `prefect-orchestration-pw4`.

## Installed at runtime

- `prefect-orchestration` — core, CLI, `AgentSession`, `BeadsStore`,
  `parsing.read_verdict`, `templates.render_template`, `telemetry`
  (once `9cn` lands).
- `po-formulas-software-dev` — ships `software-dev-full`, `epic`,
  `deployments.register()`, `mail` helper, 16 role prompts.

Install both editable into a uv tool for development:

```bash
uv tool install --force \
  --editable /path/to/prefect-orchestration \
  --with-editable /path/to/software-dev/po-formulas
```

Re-run `uv tool install --force …` any time `pyproject.toml` entry
points change (entry-point metadata is written at install time, not
on code reload).

## Related beads (read before touching core or prompts)

- `64y` TmuxClaudeBackend — lurkable sessions (shipped)
- `5kj` beads-as-mail helper (shipped; module lives in the pack)
- `shj` `po deploy` + `register()` convention (shipped)
- `9cn` OpenTelemetry / Logfire spans (open)
- `pw4` rig-path vs pack-path split (open)
- `7jr` `po run --time` for future-scheduled runs (open)
