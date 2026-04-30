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

## Rig path vs pack path (cross-repo work)

`software-dev-full` accepts an optional `--pack-path` alongside
`--rig-path`. The split:

- **`--rig-path`** — the repo where the bead lives. `bd` claim/close,
  the run_dir under `.planning/`, and the test/deploy harness all
  resolve against it.
- **`--pack-path`** — the repo where code edits + `git commit` should
  land. Defaults to `--rig-path` (the common case: bead and code in the
  same repo). Set it when the bead in repo A describes work that must
  land in repo B — e.g. `prefect-orchestration` self-dev where the
  bead lives in core but the formula code belongs in the sibling
  `po-formulas` pack.

Precedence (highest wins):

1. Explicit `--pack-path` flag on the `po run` invocation
2. Per-bead metadata `po.target_pack` (set with
   `bd update <id> --set-metadata po.target_pack=/abs/path`) — useful
   for bulk-tagging an epic's children once
3. `--rig-path` (back-compat default)

Example — PO touching its own pack:

```bash
po run software-dev-full \
  --issue-id prefect-orchestration-pw4 \
  --rig prefect-orchestration \
  --rig-path /home/me/prefect-orchestration \
  --pack-path /home/me/software-dev/po-formulas
```

The flow:

- Claims/closes the bead in `prefect-orchestration` (rig_path)
- Writes triage / plan / verdict artifacts to
  `prefect-orchestration/.planning/software-dev-full/prefect-orchestration-pw4/`
- Runs builder/linter/cleaner agents with cwd = `po-formulas/`, so
  `git add` / `git commit` land in the pack repo
- Runs baseline / regression-gate / verifier from the rig venv (the
  consumer-side suite) — "installed pack imports correctly" is the
  check that matters

Worktree-per-run isolation is a separate concern (deferred).

## Reference pack

[`../software-dev/po-formulas/`](../software-dev/po-formulas/) — actor-critic
software-dev pipeline (16 steps, 5 loops) + `epic` fan-out. Read that
if you want a concrete template to copy.

The `epic` formula's child discovery is controlled by two flags
(prefect-orchestration-h5s):

- `--discover {ids,deps,both}` — `ids` probes `<epic>.1`, `<epic>.2`, …
  (gas-city legacy convention); `deps` walks the `bd dep` graph
  (parent-child + blocks edges); `both` (default) unions the two with
  stable de-dup so dot-suffix-named *and* graph-linked children both
  fan out.
- `--child-ids a,b,c` — bypass discovery and dispatch exactly those
  ids in topo order from their `bd dep --type=blocks` edges. Useful
  when an epic root doesn't exist yet but you want to fan out a
  hand-picked set.

## Prerequisites

Install these before `uv sync` / `uv tool install`:

- **`uv`** — Python tool runner. `curl -LsSf https://astral.sh/uv/install.sh | sh`.
- **`dolt`** — sql-server backend for `bd` (beads). PO rigs default to a
  dolt sql-server so concurrent `po run` flows can claim/update beads in
  parallel without single-writer lock errors. Install:
  `curl -L https://github.com/dolthub/dolt/releases/latest/download/install.sh | bash`
  (or `brew install dolt`). See [`CLAUDE.md`](CLAUDE.md#backend-dolt-server)
  for the recommended `bd init --server …` invocation.
- **`bd`** — beads CLI on PATH. See <https://github.com/steveyegge/beads>.
- **`tmux`** *(optional)* — enables lurkable per-role agent sessions
  (`tmux attach -t po-<issue>-<role>`). PO falls back to subprocess pipes
  when tmux is missing.

## Install

**One-liner** (clones the repo to `~/.local/share/prefect-orchestration/`,
installs prerequisites if missing, symlinks the agent skill into every
detected coding agent's skill dir):

```bash
curl -fsSL https://raw.githubusercontent.com/ryanholtschneider2/prefect-agent-orchestration/main/scripts/install.sh | sh

# Knobs (env vars):
#   AGENT=claude        skill only for Claude Code (others: cursor, aider, all, none; default: all)
#   PO_REPO_URL=...     fork URL (default: this repo)
#   PO_REPO_REF=...     git ref (default: main)
#   PO_INSTALL_DIR=...  clone target (default: ~/.local/share/prefect-orchestration)
```

**From a local checkout** (development / pack authors):

```bash
git clone <repo> && cd prefect-orchestration
make install                    # CLI + skill for all detected agents
make install AGENT=claude       # CLI + skill for Claude Code only
make install AGENT=none         # CLI only, no skill
make help                       # see all targets + which agents are detected
```

Both paths run `uv tool install --editable` under the hood and symlink
`<agent-skill-dir>/po → <repo>/skills/`. On its own `po list` will show only
core formulas — run `po packs install <pack>` (or `po packs install --editable
<path>` for pack authors) to get useful output. See `po packs list` for what's
currently installed and `po packs update` if you change a pack's
`pyproject.toml` entry points and need the metadata refreshed.

## Containerized runs (k8s / docker)

`Dockerfile` (ubuntu:24.04 + node22 + tmux + uv + bd + Claude Code +
non-root `coder` user) builds `po-worker:base`; `Dockerfile.pack`
overlays a formula pack. `docker-compose.yml` runs a Prefect server +
worker locally; `k8s/*.yaml` + `k8s/po-base-job-template.json` cover
the cluster path (PVC, Secret, Deployment, base-job-template).

For a packaged install — prefect-server + po-worker + pool-register
hook + rig PVC + auth Secret references in one chart — see
[`charts/po/`](charts/po/) and the "Helm install" section in
[`engdocs/work-pools.md`](engdocs/work-pools.md). Quick start:

```bash
helm install po ./charts/po -n po --create-namespace \
    --set worker.image.repository=<registry>/po-worker \
    --set worker.image.tag=<tag>
```

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

For 100-way fanouts that bottleneck on per-account rate limits, both
modes support a **multi-account pool** (`CLAUDE_CREDENTIALS_POOL` /
`ANTHROPIC_API_KEY_POOL`, JSON arrays). Replicas pick a slot
deterministically (StatefulSet ordinal when hostname matches `*-<int>$`,
else sha256(hostname) % len); single-env always wins over its `_POOL`
counterpart. Chart wiring via `auth.{oauth,apikey}.pool.enabled`. See
[`engdocs/auth.md`](engdocs/auth.md) § "Multi-account pool".

See [`engdocs/work-pools.md`](engdocs/work-pools.md) for the full
playbook. Quick local smoke (no API key required):

```bash
mkdir -p rig && (cd rig && bd init)
ISSUE_ID=demo-1 PO_BACKEND=stub ./scripts/smoke-compose.sh
```

For the **end-to-end cluster smoke** (helm install + real
`software-dev-full` run on kind / Hetzner), see
[`engdocs/cloud-smoke.md`](engdocs/cloud-smoke.md):

```bash
./scripts/cloud-smoke/run-smoke.sh             # kind, apikey
SMOKE_DRIVER=hetzner ./scripts/cloud-smoke/run-smoke.sh
```

### Shipping Claude context to workers

Workers inside containers need the same Claude Code context as the
laptop user — `~/.claude/CLAUDE.md`, `prompts/`, `settings.json`,
`skills/`, and `commands/` — otherwise slash commands don't resolve and
the agent loses the user-global instructions baked into CLAUDE.md.

The full tree (≈4 MiB, mostly `skills/`) exceeds Kubernetes' ~1 MiB
ConfigMap budget, so PO **bakes the static tree into the worker image**
at build time, with an optional **ConfigMap overlay** for the small
subset operators iterate on (CLAUDE.md, settings.json, commands/).
Issue: `prefect-orchestration-tyf.2`.

**1. Sync local `~/.claude` into the build context** (whitelist-only,
sanitizes `settings.json`, refuses to copy credentials/history/cache):

```bash
scripts/sync-claude-context.sh --force
```

This populates `./claude-context/` (gitignored). What's included:
`CLAUDE.md`, `prompts/`, `skills/`, `commands/`, sanitized
`settings.json`. What's refused: `.credentials.json`, `projects/`,
`history.jsonl`, `cache/`, `secrets/`, `session-env/`, `ide/`,
`backups/`, `archive/`, `plans/`, `plugins/`, `memory/`, `agents/`,
`hooks/`. The sanitizer drops `hooks` / `mcpServers` / any
`*token*`/`*key*`/`*secret*` keys from `settings.json`.

**2. Build with the populated context:**

```bash
docker build \
  --build-context claude-context=./claude-context \
  -t po-worker:dev .
```

If you skip the `--build-context` flag the image still builds — the
`claude-context` stage defaults to `FROM scratch` and the COPY is a
no-op. Pod will simply lack `~/.claude/CLAUDE.md` etc., matching the
pre-tyf.2 behavior exactly.

**3. (Optional) ConfigMap override for runtime edits.** Generate and
apply an overrideable ConfigMap so you can iterate on CLAUDE.md /
settings.json / commands without rebuilding the image:

```bash
scripts/sync-claude-context.sh --force \
  --emit-configmap k8s/claude-context-overrides.yaml
kubectl apply -f k8s/claude-context-overrides.yaml
kubectl rollout restart deployment po-worker      # pickup needs restart
```

The worker Deployment + base-job-template already wire a projected
volume at `/home/coder/.claude-overrides/` with `optional: true`, so a
missing ConfigMap just means the pod runs with image-baked context.
The entrypoint `cp -rT`'s the overlay onto `~/.claude/` on boot.
Skills/prompts always come from the bake — they're too big for a
ConfigMap.

**4. Verify slash commands resolve in the pod** (manual; needs Claude
auth in the pod, so not part of the compose smoke):

```bash
kubectl exec deploy/po-worker -- claude --print /skill po
# expected: skill body, not "unknown skill"
```

A note on the **project CLAUDE.md** at `/workspace/CLAUDE.md`: the rig
PVC mount lands at `/rig` today, so `/rig/CLAUDE.md` is what's
reachable inside the pod. The `/workspace` path is wired by
`prefect-orchestration-tyf.4`.

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

## Controlling the agent (model · effort · start command)

Three knobs control the underlying `claude` invocation, each with three
override layers. Most-specific wins:

```
per-role config.toml  >  CLI flag  >  env var  >  hardcoded default
```

| Knob | Per-role file | CLI flag | Env var | Default |
|---|---|---|---|---|
| model | `agents/<role>/config.toml: model = "..."` | `--model sonnet` | `PO_MODEL=sonnet` | `opus` |
| effort | `agents/<role>/config.toml: effort = "..."` | `--effort low` | `PO_EFFORT=low` | unset (claude picks) |
| start_command | `agents/<role>/config.toml: start_command = "..."` | `--start-command "claude --foo"` | `PO_START_COMMAND="claude --foo"` | `claude --dangerously-skip-permissions` |

```bash
# One-off run with a cheaper model:
po run software-dev-full --issue-id <id> --rig <r> --rig-path <p> --model sonnet

# Pin shell-wide:
PO_MODEL=sonnet PO_EFFORT=low po run software-dev-full ...

# Per-role override (pack-author): linter on haiku, builder on opus.
# po_formulas/agents/linter/config.toml:
#   model = "haiku"
# po_formulas/agents/builder/config.toml:
#   model = "opus"
#   effort = "high"
```

`identity.toml` (persona name/email/slack/model-as-prompt-var) is
disjoint from `config.toml` (runtime knobs). Setting
`identity.toml: model = "..."` only affects the rendered prompt's
`<self>` block; use `config.toml` for runtime control.

## Telemetry / Observability

`AgentSession.prompt()` can emit one OpenTelemetry span per Claude
subprocess turn — `agent.prompt`, with attributes `role`, `issue_id`,
`session_id`, `turn_index`, `fork_session`, `model`, plus
`new_session_id` once the call returns. Spans nest under any active
parent (e.g. the enclosing Prefect task), so a Logfire / Tempo /
Honeycomb trace renders agent turns inside `build-iter-1`,
`critique-iter-1`, etc.

**Off by default.** With `PO_TELEMETRY` unset no telemetry SDK is
imported at runtime — backward-compatible for anyone not opting in.

| `PO_TELEMETRY` | Backend | Required env | Install |
|---|---|---|---|
| unset / `none` | `NoopBackend` (no-op) | — | — |
| `logfire` | `LogfireBackend` | `LOGFIRE_TOKEN` | `pip install prefect-orchestration[logfire]` |
| `otel` | `OtelBackend` (OTLP/HTTP) | `OTEL_EXPORTER_OTLP_ENDPOINT` (and optionally `OTEL_EXPORTER_OTLP_HEADERS`) | `pip install prefect-orchestration[otel]` |

### Logfire

```bash
pip install prefect-orchestration[logfire]
export LOGFIRE_TOKEN=pylf_v1_us_…
export PO_TELEMETRY=logfire
po run software-dev-full --issue-id <id> --rig <name> --rig-path <path>
```

Open the Logfire UI; every role turn shows up as an `agent.prompt`
span with `role=builder|planner|critic|…` and timing within ~50ms of
the underlying Claude subprocess wall time.

![Logfire trace](docs/img/telemetry-logfire.png)

### Generic OTLP (Tempo / Honeycomb / Datadog / Jaeger)

```bash
pip install prefect-orchestration[otel]
export PO_TELEMETRY=otel
export OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp.example.com/v1/traces
export OTEL_EXPORTER_OTLP_HEADERS="x-honeycomb-team=<key>"
po run software-dev-full --issue-id <id> --rig <name> --rig-path <path>
```

### Notes

- `issue_id` is opt-in: callers (the software-dev pack) pass it when
  constructing `AgentSession(..., issue_id=…)`. When omitted the span
  attribute is simply absent.
- The span boundary wraps **only** the Claude subprocess call so span
  duration ≈ subprocess wall time. Mail-inject and pack-overlay
  happen outside the span.
- Failed turns record the exception as a span event and set
  `status=ERROR`. The exception still propagates to the caller.

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
