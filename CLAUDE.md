# CLAUDE.md — `prefect-orchestration` / `po`

Guide for Claude Code agents (and humans) working in this repo or any
repo where `po` is installed. Load this when a task involves `po`, a
formula pack, a scheduled PO deployment, or the interaction between
beads, PO flows, and Prefect.

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

## When to use `po` vs `prefect`

| Task | Use |
|---|---|
| List installed formulas | `po list` |
| Show a formula's signature / docstring | `po show <formula>` |
| Run a formula synchronously, now | `po run <formula> --args` |
| List pack-declared deployments | `po deploy` |
| Apply pack-declared deployments to server | `po deploy --apply` |
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
