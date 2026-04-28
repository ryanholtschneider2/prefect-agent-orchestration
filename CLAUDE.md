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

### Backend (dolt-server)

PO rigs default to the **dolt sql-server** beads backend, not embedded-dolt.
Concurrent `po run` flows in the same rig hit "another process holds the
exclusive lock" under embedded-dolt — every `bd update` from a parallel
worker fails or retries. dolt-server makes parallel epics safe.

Recommended `bd init` for a new rig:

```bash
bd init --server \
        --server-host=127.0.0.1 \
        --server-port=3307 \
        --server-user=root \
        --database=<rig-slug>          # optional; otherwise prefix-derived
```

(Start the server out-of-band: `dolt sql-server -P 3307 --user root` from a
directory with the dolt database — beads data lives at `.beads/dolt`.)

`po doctor` runs `check_beads_dolt_mode` against `.beads/metadata.json` and
warns when `dolt_mode != "server"`. This rig is already on dolt-server —
see `.beads/dolt-server.port` for the live port and `.beads/metadata.json`
for the connection details.

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

# Refresh entry-point metadata after editing any pack's pyproject.toml
# (EP metadata is baked at install time, not on code reload):
po packs update
```

### Test layers

`tests/` is split by **layer**, and the layers MUST NOT OVERLAP — the PO
`software_dev_full` flow runs `unit` and `e2e` (and optionally `playwright`)
in parallel, so a misclassified test runs twice and doubles wall-clock.

| Layer | Lives at | Definition |
|---|---|---|
| **unit** | top of `tests/` (one file per module under `prefect_orchestration/`) | Individual functions / classes in isolation. **Mocking external services (HTTP, DB, subprocess) is fine.** No real network, no real subprocesses, no Prefect server. |
| **e2e** | `tests/e2e/` | Integration across **real** dependencies — subprocess-driven `po` CLI roundtrips, real `bd` shellouts, real Prefect server, real DB. No mocking of the things under integration. Slower (seconds to minutes per test). Both `po` and `bd` must be on `PATH`; many tests also need `PREFECT_API_URL` reachable. |
| **playwright** | `tests/playwright/` (when present) | Browser-driven UI tests. Skip when there is no UI to drive. |

If you write a test that mocks subprocess calls, it goes in `tests/`
(unit). If it spawns the real `po` binary or hits a real Prefect server,
it goes in `tests/e2e/`. Don't put both kinds in the same file.

The flow's `run_tests` task auto-detects which layer dirs exist in the
rig and emits the right `pytest` invocation per layer (with `--ignore`
for sibling layer dirs when no `tests/<layer>/` dir is present), so the
agent can't accidentally widen scope. See
`software-dev/po-formulas/po_formulas/software_dev.py::_build_test_cmd`.

**Per-rig layer skip via `.po-env`** — the formula reads
`<rig_path>/.po-env` (KEY=VALUE per line) at flow start and applies any
keys not already in process env. Recognised:

| Var | Effect |
|---|---|
| `PO_SKIP_E2E=1` | skip the `e2e` layer in `[lint ∥ unit ∥ e2e ∥ playwright]` fan-out |
| `PO_SKIP_PLAYWRIGHT=1` | skip the `playwright` layer (already gated on `has_ui`) |
| `PO_SKIP_UNIT=1` | skip the `unit` layer (rare — only for pure-docs rigs) |

This rig (`prefect-orchestration`) ships a `.po-env` with `PO_SKIP_E2E=1`
because `tests/e2e/` subprocesses the real `po` binary on every test
(52 tests × ~3s subprocess startup = 2+ minutes per `run_tests` call,
mostly Python import overhead). Unit tests catch the same regressions
for the kind of changes the actor-critic loop lands. Run e2e manually
before declaring a release ready: `uv run python -m pytest tests/e2e/`.

### Core module map

| Module | Role |
|---|---|
| `cli.py` | Typer entry point; discovers `po.formulas` + `po.deployments` + `po.commands` entry points; subcommands `list`/`show`/`run`/`logs`/`artifacts`/`sessions`/`watch`/`retry`/`status`/`deploy`/`doctor`/`packs` (sub-app: `install`/`update`/`uninstall`/`list`). `cli.main()` is the console-script entry: dispatches `po <command>` to `po.commands` callables, falls through to Typer for everything else. |
| `commands.py` | `po.commands` registry — `load_commands()`, `core_verbs()` (read off `app.registered_commands`), `find_command_collisions()`. |
| `packs.py` | Pack lifecycle — `install`/`update`/`uninstall`/`packs` shell out to `uv tool` and introspect `importlib.metadata` for `po.*` EP groups. |
| `agent_session.py` | `AgentSession` + `SessionBackend` Protocol (`ClaudeCliBackend`, `TmuxClaudeBackend`, `StubBackend`). Per-role `--resume <uuid>` + `--fork-session`. |
| `beads_meta.py` | `MetadataStore` Protocol; `BeadsStore` (shells `bd`) + `FileStore` (JSON fallback); `claim_issue`/`close_issue`/`list_epic_children`. |
| `parsing.py` | `read_verdict()` — reads `$RUN_DIR/verdicts/<step>.json`. |
| `templates.py` | `{{var}}` substitution over a caller-supplied agents dir (`<dir>/<role>/prompt.md`). |
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

### One-time host setup (`po serve install`)

The Prefect server's default SQLite backend deadlocks under concurrent
flows ("database is locked" once you push past ~3 parallel `po run`s).
PO ships a `serve` subcommand that installs systemd-user units for a
Postgres container + Prefect server, and points the `prefect` profile
at PG so it becomes the default backend:

```bash
po serve install        # generates a random PG password on first install,
                        # writes ~/.config/po/serve.env (mode 0600),
                        # writes ~/.config/systemd/user/{prefect-postgres,prefect-server}.service,
                        # sets PREFECT_API_DATABASE_CONNECTION_URL on the active profile,
                        # runs `prefect server database upgrade`, enables + starts both units
po serve status         # creds source + is-active + /api/health + pg_isready
po serve uninstall      # stop/disable/remove (add --purge-data to wipe the volume + serve.env)
```

Prereqs: docker, prefect on PATH, systemd user session. Run
`loginctl enable-linger $USER` once so the units survive logout.
Postgres data lives in `~/.local/share/prefect-postgres/` (bind mount
on the host so it survives container recreate).

Credentials (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`,
`PG_HOST`, `PG_PORT`, `PREFECT_API_DATABASE_CONNECTION_URL`) live in
`~/.config/po/serve.env` (mode 0600, parent dir 0700) and are sourced
by both systemd units via `EnvironmentFile=`. Re-running `po serve
install` reuses the file — no rotation unless `--rotate-password` is
passed. Override individual fields with `--pg-user`, `--pg-password`,
`--pg-db`, `--pg-host`, `--pg-port`. User-supplied values must match
`[A-Za-z0-9_.\-]+` (systemd EnvironmentFile values are written bare,
no quoting).

`--rotate-password` rotates the random password; note that the
postgres image only honors `POSTGRES_PASSWORD` on **first init** of a
data volume, so against an existing data dir you'll see a WARN —
`po serve uninstall --purge-data && po serve install` to actually
re-init, or `ALTER USER ... PASSWORD` against the running container.

`--external-pg postgresql://user:pw@host:5432/db` skips the docker
container entirely: PO writes only the prefect-server unit (without
`Requires=prefect-postgres.service`), points Prefect's profile at the
supplied URL, and runs `prefect server database upgrade` against it.
Mutually exclusive with the per-field flags.

`po serve uninstall --purge-data` removes both the PG data dir and
`~/.config/po/serve.env`. Without `--purge-data` the creds file is
left in place so a subsequent `install` resumes seamlessly.

If you'd rather not use systemd, the equivalent one-liner (with a
random password) is:

```bash
PGPASS=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
docker run -d --name prefect-postgres --restart unless-stopped \
  -e POSTGRES_USER=prefect -e POSTGRES_PASSWORD="$PGPASS" -e POSTGRES_DB=prefect \
  -p 127.0.0.1:5432:5432 \
  -v $HOME/.local/share/prefect-postgres:/var/lib/postgresql/data postgres:16-alpine
prefect config set PREFECT_API_DATABASE_CONNECTION_URL="postgresql+asyncpg://prefect:${PGPASS}@127.0.0.1:5432/prefect"
prefect server database upgrade -y
prefect server start --host 127.0.0.1 --port 4200
```

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

For trivial fanout children (e.g. the snake-bead demo) where the full
actor-critic loop would burn tokens, use `po run minimal-task` instead
— `triage → plan → build → lint → close`, fails out with no ralph
fallback when lint fails twice. See `engdocs/minimal-task.md`.

### Running an epic (DAG fan-out)

```bash
po run epic \
  --epic-id <epic-id> \
  --rig <name> \
  --rig-path <path>

# Knobs:
#   --max-issues N                       # process only the first N topo-sorted children
#   --dry-run                            # exercise the DAG without spawning Claude
#   --discover {ids,deps,both}           # discovery mode (default: both)
#   --child-ids id1,id2,id3              # explicit override; bypass discovery
```

`epic_run` is now a thin wrapper over `graph_run` (see next section).
Discovery is controlled by two flags (prefect-orchestration-h5s):

- `--discover` chooses how children are found:
  - `ids`  — probe `<epic>.1`, `<epic>.2`, … (gas-city legacy convention).
  - `deps` — walk the `bd dep` graph (parent-child + blocks edges).
  - `both` — union of the two with stable de-dup (default). Picks up
    both dot-suffix-named children and ones linked only via `bd dep`.
- `--child-ids a,b,c` bypasses discovery entirely and dispatches exactly
  those ids in topo order built from their `bd dep --type=blocks`
  edges. Each id must exist and be open; closed ids raise (reopen
  with `bd update <id> --status open` first).

```bash
# Force the legacy dot-suffix probe (e.g. on a real legacy epic that
# never had bd-dep edges populated):
po run epic --epic-id prefect-orchestration-3cu --rig <name> --rig-path <path> \
            --discover ids

# Dispatch a hand-picked set of beads as a graph (no epic naming needed):
po run epic --epic-id prefect-orchestration-h5s --rig <name> --rig-path <path> \
            --child-ids prefect-orchestration-5i9,prefect-orchestration-1ij,prefect-orchestration-dmy
```

### Running an arbitrary sub-graph

`po run graph --root-id <id>` generalises the epic case: any bead can
be a root, and the DAG is built from `bd dep` edges directly — no
naming convention or `epic` status required.

```bash
# Fan out a feature bead's sub-tasks (linked via `bd dep add`)
po run graph --root-id my-feature-1 \
  --rig <name> \
  --rig-path <path>

# Convoy / ad-hoc grouping bead, including the root itself
po run graph --root-id release-blockers-q2 \
  --rig <name> \
  --rig-path <path> \
  --root-as-node

# Knobs:
#   --traverse=parent-child,blocks,tracks   # which edge types to follow
#                                           # default: parent-child,blocks
#   --formula=software-dev-full             # formula to run per node
#                                           # default: software-dev-full
#   --max-issues=N                          # cap topo-prefix
#   --include-closed                        # bring closed beads back in
#                                           # (re-run / verification)
#   --root-as-node                          # include root in submitted set
```

Discovery is BFS-up via `bd dep list <id> --direction=up --type=<edge>`,
ordering is topo over the `blocks`-only sub-graph; cycles raise
`dependency cycle: [ids...]`. The chosen formula must accept
`(issue_id, rig, rig_path)` plus optional `parent_bead` / `dry_run`.

### Running an ad-hoc scratch flow

For one-offs without a registered formula / installed pack:

```bash
po run --from-file ./scratch.py [--name <flow>] [--key value ...]
```

PO imports the file, finds the `@flow` callable (auto-detect for a
single flow; pass `--name` to disambiguate), and dispatches it through
the same kwargs-parsing / `_autoconfigure_prefect_api()` path as a
registered formula. Same Prefect semantics, same UI artifacts. Scratch
flows aren't entry-point-registered so they don't show up in `po list`,
and `po logs/artifacts/watch` only apply if the scratch flow itself
takes `issue_id` and writes a run_dir. Runs arbitrary Python in-process
— local dev tool only.

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

### Telemetry (Logfire / OTel)

`AgentSession.prompt()` can wrap each Claude subprocess turn in an
OTel span (`agent.prompt` with `role`/`issue_id`/`session_id`/
`turn_index`/`fork_session`). Off by default; opt in via
`PO_TELEMETRY=logfire` (needs `LOGFIRE_TOKEN`) or `PO_TELEMETRY=otel`
(needs `OTEL_EXPORTER_OTLP_ENDPOINT`). Optional extras:
`pip install prefect-orchestration[logfire]` or `[otel]`. Spans nest
under any active Prefect task span automatically. Full env-var matrix
+ OTLP example in the README "Telemetry / Observability" section.

### Containerized runs (compose / k8s)

`Dockerfile` (ubuntu:24.04 + node22 + tmux + uv + bd + Claude Code +
core, non-root `coder` user) builds the base image; `Dockerfile.pack`
overlays a formula pack on top. `docker/entrypoint.sh` writes
`~/.claude.json` from `ANTHROPIC_API_KEY` so Claude Code skips
onboarding without a TTY. Tmux is installed but the runtime backend
picker (`prefect_orchestration.backend_select.select_default_backend`)
falls back to `ClaudeCliBackend` whenever stdout is non-TTY (the pod
case); `ENV PO_BACKEND=cli` is set on the image to make the choice
loud. Local smoke:

```bash
mkdir -p rig && (cd rig && bd init)
ISSUE_ID=demo-1 PO_BACKEND=stub ./scripts/smoke-compose.sh
```

K8s manifests live under `k8s/` (PVC, Secret stub, Deployment,
base-job-template). Apply order + full playbook in
`engdocs/work-pools.md`. `po doctor` warns when a pack-declared
deployment pins a `work_pool_name` that isn't on the server — see
`check_deployment_pools_exist` in `prefect_orchestration/doctor.py`.

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

### `PO_FORMULA_MODE` — legacy vs graph dispatch (7vs.5)

`software_dev_full` ships two implementations behind one entry point.
`PO_FORMULA_MODE=legacy` (default) runs the 305-line nested-loop body
unchanged. `PO_FORMULA_MODE=graph` runs a thin seed-bead author + a
bounded watcher loop around `graph_run`; loop bodies move into the
bead graph itself, with critic agents creating iter+1 beads on
rejection.

```bash
# Default: legacy nested-loop body.
po run software-dev-full --issue-id <id> --rig <name> --rig-path <path>

# Graph mode: 19-node seed sub-graph, reactive critic-driven extension.
PO_FORMULA_MODE=graph po run software-dev-full \
  --issue-id <id> --rig <name> --rig-path <path>

# Per-rig opt-in via .po-env:
echo "PO_FORMULA_MODE=graph" >> <rig>/.po-env
```

Background and rationale: `engdocs/formula-modes.md`. Migration plan
(7vs.5 ships the flag; a follow-up issue flips the default to graph;
7vs.6 deletes the legacy body).

## When to use `po` vs `prefect`

| Task | Use |
|---|---|
| List installed formulas | `po list` |
| Show a formula's signature / docstring | `po show <formula>` |
| Run a formula synchronously, now | `po run <formula> --args` |
| Fan out an arbitrary bd sub-graph rooted at any bead | `po run graph --root-id <id> --rig <name> --rig-path <path> [--traverse=parent-child,blocks] [--formula=software-dev-full]` |
| Run an ad-hoc `@flow` from a `.py` file (no install required) | `po run --from-file <path> [--name <flow>] --args` |
| Tail / follow logs for an issue's run | `po logs <issue-id> [-f] [-n N] [--file NAME]` |
| Dump full forensic trail for a run | `po artifacts <issue-id> [--verdicts] [--open]` |
| Show per-role Claude session UUIDs for a run | `po sessions <issue-id> [--resume <role>]` |
| Attach to an issue's tmux session (k8s pod or host) | `po attach <issue-id> [--role <role>] [--list] [--print-argv]` |
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

## Prompt layout (per-pack)

Packs author prompts as plain markdown under
`po_formulas/agents/<role>/prompt.md` — one folder per agent role,
leaving room for an optional sibling `config.toml` later (model choice,
option defaults). `templates.render_template(agents_dir, role, **vars)`
resolves `<agents_dir>/<role>/prompt.md` and substitutes `{{var}}`.
Hyphens are fine in role names (`plan-critic`, `regression-gate`).

```
po_formulas/agents/
  triager/      prompt.md
  baseline/     prompt.md
  planner/      prompt.md
  plan-critic/  prompt.md
  builder/      prompt.md
  build-critic/ prompt.md
  linter/       prompt.md
  tester/       prompt.md
  regression-gate/  prompt.md
  deploy-smoke/ prompt.md
  review-artifacts/ prompt.md
  verifier/     prompt.md
  ralph/        prompt.md
  documenter/   prompt.md
  demo-video/   prompt.md
  learn/        prompt.md
```

**No Jinja, no `{% include %}`, no fragment auto-compose.** If two
roles share rubric, duplicate it — grep-able beats clever. Role names
in `render(...)` are *prompt-file lookup keys*, decoupled from
`RoleRegistry` keys / task names / verdict-file basenames (those stay
stable across renames so per-role Claude session UUIDs don't orphan).
See `engdocs/principles.md` § "Prompt authoring convention".

## Installed at runtime

- `prefect-orchestration` — core, CLI, `AgentSession`, `BeadsStore`,
  `parsing.read_verdict`, `templates.render_template`, `telemetry`
  (once `9cn` lands).
- `po-formulas-software-dev` — ships `software-dev-full`, `epic`,
  `deployments.register()`, `mail` helper, 16 role prompts.

Install both editable for development — use `po packs install --editable`
(which shells out to uv under the hood):

```bash
# First time: bootstrap core from PyPI or an editable path, then add
# the pack(s) you're working on:
po packs install --editable /path/to/prefect-orchestration
po packs install --editable /path/to/software-dev/po-formulas
```

Run `po packs update` any time a pack's `pyproject.toml` entry points
change — entry-point metadata is written at install time, not on
code reload, and `po packs update` refreshes it for every installed pack.
`po packs list` lists what's installed and what each contributes.

### Pack-contributed `po doctor` checks (`po.doctor_checks`)

Packs can ship their own health checks for `po doctor` via the
`po.doctor_checks` entry-point group. Each entry resolves to a zero-arg
callable returning a `prefect_orchestration.doctor.DoctorCheck`:

```python
# po_formulas/checks.py
from prefect_orchestration.doctor import DoctorCheck

def claude_cli_present() -> DoctorCheck:
    return DoctorCheck(
        name="claude CLI present",
        status="green",   # green | yellow | red
        message="claude 0.x.y",
        hint="install Claude Code if absent",  # printed under non-green rows
    )
```

```toml
# pyproject.toml
[project.entry-points."po.doctor_checks"]
claude-cli-present = "po_formulas.checks:claude_cli_present"
```

`po doctor` runs core checks first (bd, Prefect server, pools, entry
points, …), then each pack's checks in deterministic alphabetical
order by distribution name, into one unified table with a `SOURCE`
column showing pack provenance. Each pack check is wrapped in a 5-second
soft timeout; on timeout the row is yellow (warn), not red. Any red row
exits 1.

Run `po packs update` after registering a new check so `importlib.metadata`
sees the new entry-point.

### Pack-shipped utility commands (`po.commands`)

Packs can ship lightweight, **non-orchestrated** utility ops via the
`po.commands` entry-point group. These dispatch as `po <command>` —
NOT `po run <command>` — and skip Prefect overhead entirely
(principle §4: utility ops are direct callables, not flows).

```python
# po_formulas/commands.py
def summarize_verdicts(issue_id: str) -> None:
    """One-line summary per verdicts/*.json for an issue's run dir."""
    ...
```

```toml
# pyproject.toml
[project.entry-points."po.commands"]
summarize-verdicts = "po_formulas.commands:summarize_verdicts"
```

Then:

```bash
po summarize-verdicts --issue-id prefect-orchestration-4ja.5
```

`po list` shows formulas + commands together with a `KIND` column
(`formula` | `command`); `po show <name>` works for both.

**Argument parsing**: identical to `po run` — `--key value`,
`--key=value`, bare `--flag` (→ `True`), `--no-flag` (→ `False`).
Values are coerced to bool/int/float when unambiguous.

**Collision handling**: at `po packs install` / `po packs update` time, the
post-install scan refuses any pack whose `po.commands` entry shadows
a core Typer verb (`run`, `list`, `show`, `deploy`, …). The pack stays
installed but the install command exits non-zero — fix the pack's
`pyproject.toml` and reinstall, or `po packs uninstall <pack>`.

Run `po packs update` after registering a new command so `importlib.metadata`
sees the new entry-point.

## Related beads (read before touching core or prompts)

- `64y` TmuxClaudeBackend — lurkable sessions (shipped)
- `5kj` beads-as-mail helper (shipped; module lives in the pack)
- `shj` `po deploy` + `register()` convention (shipped)
- `9cn` OpenTelemetry / Logfire spans (open)
- `pw4` rig-path vs pack-path split (shipped — see README §"Rig path vs pack path")
- `7jr` `po run --time` for future-scheduled runs (open)
