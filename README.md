# prefect-orchestration

**Core library + pluggable `po` CLI** for running Prefect-based multi-agent
workflows on top of the Claude Code CLI (or any subprocess-driven coding agent).

Ships no formulas of its own. Every pipeline — `software-dev-full`,
future `bio-experiment`, `microcorp`, etc. — lives in a separate pack that
registers itself via entry points.

## What's in core

| Module | Purpose |
|---|---|
| `agent_session.AgentSession` | Per-role wrapper around `claude --print --resume <uuid>`; persistent session context across turns; `--fork-session` support. |
| `agent_session.SessionBackend` (Protocol) | Swap `ClaudeCliBackend` → `StubBackend` (dry-run), future `TmuxClaudeBackend` (lurk-able), `ClaudeAgentSdkBackend` (commercial API key), `GeminiCliBackend`, etc. |
| `beads_meta.MetadataStore` (Protocol) | `BeadsStore` (uses `bd` CLI) or `FileStore` (local JSON fallback when beads isn't present). |
| `beads_meta.{claim_issue, close_issue, list_epic_children}` | Minimal beads tracker ops; no-op when `bd` is absent. |
| `parsing.read_verdict` | Reads `$RUN_DIR/verdicts/<name>.json` — the artifact convention for agent → orchestrator signals. |
| `templates.render_template` | `{{var}}` substitution over a caller-supplied prompts directory. |
| `cli.app` (`po`) | Discovers formulas via the `po.formulas` entry-point group; `po list` / `po show <name>` / `po run <name> --args`. |

## Writing a pack

A pack is a regular Python package with `@flow`s and entry points:

```toml
# my-pack/pyproject.toml
[project]
dependencies = ["prefect-orchestration", "prefect>=3.0"]

[project.entry-points."po.formulas"]
my-flow = "my_pack.flows:my_flow"
```

```python
# my-pack/my_pack/flows.py
from prefect import flow
from prefect_orchestration.agent_session import AgentSession, ClaudeCliBackend

@flow
def my_flow(issue_id: str, rig_path: str, dry_run: bool = False) -> dict:
    sess = AgentSession(role="doer", repo_path=..., backend=ClaudeCliBackend())
    sess.prompt("do the thing")
    return {"done": True}
```

Then anywhere `prefect-orchestration` + `my-pack` are installed:

```bash
po list            # shows my-flow
po run my-flow --issue-id foo --rig-path /some/path
```

## Deployments (cron / interval / manual)

For scheduled or long-lived workflows, packs ship Prefect deployments
via the `po.deployments` entry-point group. Each entry point names a
`register()` callable that returns one or more `RunnerDeployment`
objects (built from `flow.to_deployment(...)`).

```toml
# my-pack/pyproject.toml
[project.entry-points."po.deployments"]
my-pack = "my_pack.deployments:register"
```

```python
# my-pack/my_pack/deployments.py
from prefect.schedules import Cron
from my_pack.flows import my_flow

def register():
    return [
        my_flow.to_deployment(
            name="my-flow-nightly",
            schedule=Cron("0 9 * * *", timezone="America/New_York"),
            parameters={"issue_id": "sr-8yu"},
        ),
    ]
```

`register()` should be pure — construct and return deployment objects, no
network I/O. It is called eagerly by `po deploy` for listing.

```bash
po deploy                          # list every registered deployment
po deploy --pack my-pack           # filter
po deploy --apply                  # upsert to the Prefect server
po deploy --apply --work-pool po   # also assign a work pool
```

`--apply` requires `PREFECT_API_URL` pointing at a running server
(`prefect server start` → `http://127.0.0.1:4200/api`). Deployments are
upserted by `(flow_name, name)` so re-running is idempotent.
For runs to actually execute, start a worker against the same work pool:

```bash
prefect work-pool create po --type process
prefect worker start -p po
```

Event-triggered deployments: leave the schedule off and wire triggers in
Prefect's UI (Automations).

## Reference pack

[`../software-dev/po-formulas/`](../software-dev/po-formulas/) — actor-critic
software-dev pipeline (16 steps, 5 loops) + `epic` fan-out. Read that
if you want a concrete template to copy.

## Install

```bash
cd prefect-orchestration
uv sync
```

That gets you the `po` CLI and the library. On its own `po list` will show
no formulas — install or `uv add` a pack to get useful output.

## Design principles

- **Claude Code CLI is the worker, Prefect is the foreman.** Core doesn't
  model agents; it models subprocess sessions. Formula packs compose those
  into DAGs.
- **Verdicts are file artifacts, not LLM reply regex.** Agents write
  `$RUN_DIR/verdicts/<step>.json`; orchestrators read them back. No
  parsing prose.
- **Beads is the source of truth for task structure.** Dependencies map
  directly onto Prefect `wait_for=`. No polling daemon needed.
- **Concurrency is a deploy concern, not a code concern.** Prefect
  work-pool + tagged concurrency limits cover per-role + total-workers;
  we don't reinvent that.
- **No venv gymnastics.** Packs are regular Python deps. Entry points
  are regular entry points. `po` is a regular Typer CLI.
