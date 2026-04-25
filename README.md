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
| `agent_session.SessionBackend` (Protocol) | Swap `ClaudeCliBackend` → `StubBackend` (dry-run) → `TmuxClaudeBackend` (lurk-able via `tmux attach -t po-<issue>-<role>`); future `ClaudeAgentSdkBackend` (commercial API key), `GeminiCliBackend`, etc. |
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
no formulas — run `po install <pack>` to get useful output. For pack
authors: `po install --editable <path>`. See `po packs` for what's
currently installed and `po update` if you change a pack's
`pyproject.toml` entry points and need the metadata refreshed.

## Containerized runs (k8s / docker)

`Dockerfile` (ubuntu:24.04 + node22 + tmux + uv + bd + Claude Code +
non-root `coder` user) builds `po-worker:base`; `Dockerfile.pack`
overlays a formula pack. `docker-compose.yml` runs a Prefect server +
worker locally; `k8s/*.yaml` + `k8s/po-base-job-template.json` cover
the cluster path (PVC, Secret, Deployment, base-job-template).
### Auth modes

Workers support two auth paths; the entrypoint picks one at startup
(OAuth wins when both are set):

1. **OAuth (Claude.ai subscription)** — preferred for non-prod / dev.
   Set `CLAUDE_CREDENTIALS` to the contents of `~/.claude/.credentials.json`
   and the entrypoint materializes it to `/home/coder/.claude/.credentials.json`
   (mode 0600) before exec. `ANTHROPIC_API_KEY` is unset in this mode so
   the SDK doesn't silently prefer the key.

   *k8s recipe:*
   ```bash
   kubectl create secret generic claude-oauth \
       --from-file=credentials.json="$HOME/.claude/.credentials.json"
   # then in the worker Deployment env:
   #   - name: CLAUDE_CREDENTIALS
   #     valueFrom: { secretKeyRef: { name: claude-oauth, key: credentials.json } }
   ```
   Template at [`k8s/claude-oauth.example.yaml`](k8s/claude-oauth.example.yaml).

   *Local docker-compose recipe:* uncomment the bind-mount in
   `docker-compose.yml`:
   ```yaml
   - ${HOME}/.claude/.credentials.json:/home/coder/.claude/.credentials.json:ro
   ```
   No env-var copy is needed — the entrypoint detects a pre-existing
   credentials file and switches to OAuth automatically.

2. **API key (`ANTHROPIC_API_KEY`)** — production fallback; matches the
   k8s default in [`k8s/po-worker-deployment.yaml`](k8s/po-worker-deployment.yaml).
   Mounted as a Secret in k8s, host env var locally. The entrypoint
   bootstraps `~/.claude.json` with a `customApiKeyResponses` approval
   block so Claude Code skips onboarding without a TTY.

   *k8s recipe:*
   ```bash
   kubectl create secret generic anthropic-api-key \
       --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"
   ```
   Template at [`k8s/anthropic-api-key.example.yaml`](k8s/anthropic-api-key.example.yaml).

See [`engdocs/work-pools.md`](engdocs/work-pools.md) for the full
playbook. Quick local smoke (no API key required):

```bash
mkdir -p rig && (cd rig && bd init)
ISSUE_ID=demo-1 PO_BACKEND=stub ./scripts/smoke-compose.sh
```

## Agent messaging (beads-as-mail)

Mid-run handoff between roles (critic → builder, verifier → doer, …) is
done with `po_formulas.mail` — a thin wrapper over `bd` that turns the
beads tracker into a shared mailbox. No new MCP, no extra daemon.

```python
from po_formulas.mail import send, inbox, mark_read

# Critic, after reviewing builder's iter 1:
send("builder", "fix X", "mail.py:42 swallows parse errors", from_agent="critic")

# Builder, at the top of its next turn:
for msg in inbox("builder"):
    # ... address msg.subject / msg.body ...
    mark_read(msg.id)
```

Conventions:

- Mail is stored as `type=task`, `priority=4` (backlog, hidden from
  `bd ready`), labels `mail` + `mail-to:<recipient>`, assignee = recipient.
- Title format: `[mail:<to>] <subject>`; description carries the body
  plus a `From:` footer.
- Role prompts should include [`po_formulas/mail_prompt.md`](po_formulas/mail_prompt.md)
  so every turn starts with an inbox check.
- Escalation: if you need threads, read receipts, or file reservations,
  switch to `mcp-agent-mail`.

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
