# Pack convention

The canonical shape of a PO pack. One source of truth for pack authors;
everything else in engdocs should point here.

## What a pack is

A **Python package** (has `pyproject.toml`) that contributes one or
more of:

- **Flows** — orchestrated multi-step work (via `po.formulas`)
- **Deployments** — scheduled / manual Prefect deployments (via `po.deployments`)
- **Commands** — ad-hoc utility ops (via `po.commands`, invoked as `po <command>`)
- **Doctor checks** — health checks (via `po.doctor_checks`)
- **Skills** — Claude Code skills that teach agents how to use tools (via directory convention)
- **Overlay files** — content copied into the agent's `cwd` at session start (via directory convention)
- **Python deps** — vendor SDKs/CLIs the pack needs (via `pyproject.toml [project.dependencies]`)

A pack can contribute any subset. A "tool pack" (like `po-stripe`)
typically contributes skills + commands + doctor checks + a Python dep.
A "formula pack" (like `po-formulas-software-dev`) contributes flows +
deployments + agent prompts.

## Directory layout

```
<pack-name>/                           (e.g. po-stripe, po-formulas-software-dev)
├── pyproject.toml                     entry-points, deps, version
├── po_<module>/                       importable Python module
│   ├── __init__.py
│   ├── flows.py                       @flow definitions → po.formulas
│   ├── deployments.py                 register() → po.deployments
│   ├── commands.py                    functions → po.commands
│   ├── checks.py                      DoctorCheck functions → po.doctor_checks
│   ├── cli.py                         if the pack ships a sub-CLI
│   └── agents/<role>/
│       ├── prompt.md                  per-role prompts for flow steps (4ja.3)
│       ├── identity.toml              optional per-role identity (o2r)
│       └── memory/MEMORY.md           optional per-role persistent memory (4xo)
├── skills/                            Claude Code skills
│   └── <skill-name>/
│       └── SKILL.md                   YAML frontmatter + markdown body
├── overlay/                           files merged into rig cwd at session start (4ja.4)
│   └── **                             anything the pack wants present (CLAUDE.md, .env.example, scripts/, …)
├── README.md                          human-facing doc
└── CLAUDE.md                          agent-facing doc (optional)
```

Only `pyproject.toml` is mandatory. Every other directory is optional
and activates its feature only if present.

## pyproject.toml shape

```toml
[project]
name = "po-stripe"
version = "0.1.0"
dependencies = ["stripe>=9.0"]          # vendor SDK; pip installs it

[project.entry-points."po.commands"]
stripe-balance = "po_stripe.commands:balance"
stripe-recent  = "po_stripe.commands:recent_charges"

[project.entry-points."po.doctor_checks"]
stripe-env = "po_stripe.checks:env_set"
stripe-api = "po_stripe.checks:api_reachable"

# Formula pack version:
[project.entry-points."po.formulas"]
software-dev-full = "po_formulas.flows:software_dev_full"
epic              = "po_formulas.flows:epic_run"

[project.entry-points."po.deployments"]
software-dev = "po_formulas.deployments:register"
```

## Skills — how they reach agents

Pack ships `skills/<skill-name>/SKILL.md`. On session start, the
overlay mechanism (see `4ja.4`) copies every installed pack's
`skills/` dir into the rig's `.claude/skills/<pack-name>/`. Claude
Code auto-picks up skills from there — no PO-side glue.

Skill format is the standard Claude Code skill:

```markdown
---
name: stripe
description: Charge customers, issue refunds, inspect balances via the Stripe API.
---

# Stripe skill

In THIS nanocorp:

- Always use test keys (`STRIPE_API_KEY` starts with `sk_test_`) in dev.
- Charges over $500 require human approval — `bd human <issue> --question="approve $<amt> charge to <customer>"` and wait.
- Always pass an idempotency key derived from the bead ID + step name.

## Commands

```
import stripe, os
stripe.api_key = os.environ["STRIPE_API_KEY"]

charge = stripe.PaymentIntent.create(
    amount=2000,
    currency="usd",
    idempotency_key=f"{issue_id}:{step}",
)
```

## Doc pointer
https://docs.stripe.com/api
```

## Overlay — what it is, what belongs in it

The overlay is for files the agent's working directory should contain
when a session starts. Typical contents:

- `overlay/CLAUDE.md` — pack-specific agent instructions that
  reinforce the skill (repetition is fine — prompts are cheap)
- `overlay/scripts/` — helper shell scripts the agent can call
- `overlay/.env.example` — document required env vars (not real secrets)
- `overlay/prompts/` — rendered prompt templates the agent might include

Overlay does NOT contain secrets, rig-specific config, or anything
that should survive session end. It's pack-authored, pack-versioned,
idempotent to copy.

### Mechanics (4ja.4)

`AgentSession.prompt()` lazily walks every installed pack via
`importlib.metadata` (any distribution with a `po.formulas`,
`po.commands`, `po.doctor_checks`, or `po.deployments` entry point)
and copies its overlay/skills content into the rig once per session:

| Source | Destination | Conflict policy |
|---|---|---|
| `<pack>/overlay/**` | `<rig>/<rel>` | **skip-existing** (filesystem presence) |
| `<pack>/<module>/agents/<role>/overlay/**` | `<rig>/<rel>` | **skip-existing** — laid down *before* pack-wide so role files win |
| `<pack>/skills/<name>/**` | `<rig>/.claude/skills/<pack-name>/<name>/**` | **always overwrite** |

Skip-existing uses filesystem presence (not git status) for v1 —
simpler, no `git` shell-out per file, and matches the AC literal
("existing files in cwd not overwritten"). User-authored files in
the rig always win.

Skills are pack-owned canonical content and always overwrite. We
only touch `.claude/skills/<pack-name>/` for installed packs;
sibling `.claude/skills/<other>/` dirs (user-authored, plugin) are
left alone.

**Per-role precedence.** `agents/<role>/overlay/**` is processed
*before* `overlay/**`. Once a role file lands, the pack-wide overlay
sees it as "existing" and skips. So role-specific files cleanly
override pack-wide ones on conflict, and a session with no matching
role overlay falls through to the pack-wide content.

**Wheel vs editable layout.** Discovery probes `<dist-root>/overlay/`
first (editable installs ship `overlay/` next to `pyproject.toml`),
then `<package-root>/overlay/` (wheels typically embed it inside the
importable module via `[tool.hatch.build.targets.wheel] include`).
Same probe for `skills/`. Pick whichever fits your build; both work.

**Opt-out.** Per-session: `AgentSession(overlay=False, skills=False)`.
Materialization is best-effort — exceptions are logged and the turn
proceeds.

**Cleanup.** None in v1. Overlay files persist beyond session end;
add them to `.gitignore` if the rig is git-tracked. Cleanup may
arrive in a later issue.

**Concurrency.** Two sessions racing into the same rig will each
attempt the copy; skip-existing makes the second a near-no-op
(stat per file). The TOCTOU window is tiny and identical bytes
on both sides, so collisions don't corrupt content.

## Per-role identity (o2r)

Each role gets a stable identity (display name, email, slack handle,
mail-server agent name, model preference). Convention:

```
<pack>/po_<module>/agents/<role>/identity.toml
```

Schema (all fields optional):

```toml
[identity]
name             = "acquisitions-bot"
email            = "acquisitions@nanocorp.example"
slack            = "@acquisitions-bot"
mail_agent_name  = "acquisitions-bot"   # falls back to name when absent
model            = "opus"
```

When present, `prefect_orchestration.templates.render_template` auto-
prepends a `<self>...</self>` block to every rendered prompt and
exposes the fields as `{{agent_name}}`, `{{agent_email}}`,
`{{agent_slack}}`, `{{agent_mail_name}}`, `{{agent_model}}`
substitution variables. Roles **without** an `identity.toml` render
unchanged (no `<self>` block, no auto-vars) — fully backward-compatible.

### Per-rig overlay precedence

A rig can override the pack default by shipping:

```
<rig>/.claude/agents/<role>/identity.toml
```

**Per-field merge** (rig wins per key, pack fills the rest). To
override only the display name, the rig file needs only:

```toml
[identity]
name = "acquisitions-bot-staging"
```

…and `email`, `slack`, etc. still come from the pack. This lets the
same pack ship as a different "person" per rig without copying every
field.

### `register_agent` integration

`mcp-agent-mail` requires every agent to register before reservations
or messaging. Prompts that run the registration handshake should use
`{{agent_name}}` so the orchestrator and the agent agree on identity:

```
mcp-agent-mail register_agent \
    project_key="…" \
    name="{{agent_name}}" \
    program="claude-code" \
    model="{{agent_model}}"
```

When no `identity.toml` is present, prompts may continue to fall back
to the legacy `{{issue_id}}-{{role}}` naming. Once a role has an
identity, prefer `{{agent_name}}` — it's stable across runs of the
same role in the same rig.

### Notes

- TOML parse errors raise `IdentityLoadError` (subclass of
  `ValueError`) with the offending path — failures are loud, not
  silently anonymous.
- Unknown keys in `[identity]` are ignored (forward-compat).
- Don't hand-roll a `<self>` block in your `prompt.md` once
  `identity.toml` is present — you'd get two.

## Per-role memory (4xo)

Each role can carry persistent memory across runs without rebuilding
context every turn. Convention mirrors Claude Code's auto-memory at
`~/.claude/projects/<slug>/memory/`:

```
<pack>/po_<module>/agents/<role>/memory/MEMORY.md   pack default (optional)
<rig>/.claude/agents/<role>/memory/MEMORY.md        rig overlay (optional)
```

`render_template(agents_dir, role, rig_path=...)` checks both paths.
The **rig overlay wins** when present (file-level precedence — MEMORY.md
is unstructured prose, not a config we can per-line merge); the pack
default is the fallback. Rig overlay is the natural place for
**agent-written** memory because the rig is the writable, run-local
location; the pack default is for **shipped baseline knowledge** the
pack author wants every consumer to start with.

When found, the file's raw contents are wrapped as:

```
<memory>
...file contents verbatim...
</memory>
```

…and **prepended outside** the `<self>` block. Final ordering of a
rendered prompt:

```
<memory>            (if any)
<self>              (if identity.toml present)
…prompt body…
```

`AgentSession.prompt()` later prepends `<mail-inbox>` per turn, so the
delivered prompt becomes `<mail-inbox>` → `<memory>` → `<self>` →
body. Mail is the most time-sensitive context (this turn's news) and
sits outermost; memory is older, curated context.

### Properties

- **Verbatim**: no `{{var}}` substitution inside the memory block.
  Agent-authored content may contain literal `{{...}}` text safely.
- **Empty file is no block**: a whitespace-only `MEMORY.md` renders
  nothing (no empty `<memory></memory>`).
- **No size cap (v1)**: matches Claude Code, which has no enforced
  cap. The agent owns its own memory file and is expected to curate
  it. A future bead can add soft truncation if context bloat becomes
  an issue. Mail's `MAX_INBOX_MESSAGES = 20` cap exists because mail
  is multi-message and grows unboundedly; memory is a single file the
  agent itself maintains.
- **No migration**: roles without a `memory/` dir render exactly as
  before (backwards compatible).
- **Agent-managed I/O**: PO does not write to `MEMORY.md`. The agent
  reads and writes via normal file ops; PO just exposes the content
  on every turn.
- **Out of scope** (separate beads if they ever land): server-side
  memory, vector retrieval, cross-role memory sharing, structured
  per-topic files indexed from `MEMORY.md`. v1 is a single file per
  role, on disk, like Claude Code.

## Tool-access preference order

When a skill teaches an agent to use a tool, prefer interfaces in
this order:

1. **CLI** (native binary or Python script on `PATH`). Preferred.
   Agents write shell commands faster than they construct SDK calls,
   and `|`, `jq`, `grep` compose naturally. `stripe`, `gh`, `gcloud`,
   `slack-cli`, `bd`, `po` — all CLIs first.
2. **SDK** (Python library). Use when the CLI can't express the
   operation (streaming, specialized types, webhooks). Fall back
   here; don't start here.
3. **HTTP API** (direct `httpx`/`curl`). Use only when neither CLI
   nor SDK covers the endpoint. Usually means the tool is immature.
4. **MCP server**. Last resort — adds a subprocess, a protocol
   layer, and stateful session coupling. Use only when a stateful
   multi-turn interaction with the tool is needed that a CLI can't
   match.

A skill should **lead** with the highest available tier and
document the lower tiers as fallbacks. When a provider ships
their own Claude Code skill or `llms.txt`, **link to it** from the
pack's SKILL.md rather than duplicating — our skill adds nanocorp-
specific policy (idempotency conventions, budget thresholds,
project-key discipline) on top of the vendor's canonical guidance.

### Official vendor skills / llms.txt — link, don't duplicate

Many vendors now publish LLM-friendly docs:

- Stripe: https://docs.stripe.com/llms.txt
- Claude's own API docs: https://docs.claude.com/llms.txt
- Prefect: https://docs.prefect.io/llms.txt
- Others: a growing list; check `<vendor-docs-site>/llms.txt` or
  their "for AI agents" / skills page before writing a skill.

Pack's SKILL.md format:

```markdown
---
name: stripe
description: Charge customers, issue refunds, inspect balances via Stripe.
---

# Stripe skill — <this-nanocorp> conventions

## Canonical vendor docs
- CLI reference: https://docs.stripe.com/stripe-cli
- API reference: https://docs.stripe.com/api
- Vendor llms.txt: https://docs.stripe.com/llms.txt

## This nanocorp's rules
(idempotency, bd human on > $500, test-key-in-dev, …)

## Quick CLI recipes
(stripe charges create …, stripe refunds create …, stripe balance …)

## SDK fallback
(import stripe; stripe.PaymentIntent.create(…) — when streaming / webhooks)
```

Keep the skill short. The pack owns policy and conventions; the
vendor owns mechanics.

## Native-binary prerequisites

When a pack depends on a **non-Python** tool (Stripe CLI, `gh`, `ffmpeg`,
cloud-provider CLIs, …) the pack does **not** install it. Instead:

1. The pack's `SKILL.md` documents the prerequisite with platform-
   specific install commands (`brew install stripe/stripe-cli/stripe`
   for macOS, `apt install …` for Debian, direct-download URL for
   others).
2. A `po.doctor_checks` entry verifies the binary is on `PATH` (e.g.,
   `shutil.which("stripe")`) and its `--version` meets any minimum.
3. On missing, the check returns `red` with the install command as
   the hint — `po doctor` surfaces it.

No post-install hooks, no shell-script auto-install. Running arbitrary
code at pack-install time is a supply-chain risk we refuse on
principle. Users install natives once; `po doctor` nags until
resolved.

## Credentials

Env vars. No `CredentialProvider` Protocol today (per `principles.md
§5`). A pack's SKILL.md and/or `overlay/CLAUDE.md` documents the
required variable names. `po.doctor_checks` verifies presence.

Example (in `po-stripe/po_stripe/checks.py`):

```python
import os

def env_set():
    key = os.environ.get("STRIPE_API_KEY")
    if not key:
        return ("red", "STRIPE_API_KEY unset",
                "export STRIPE_API_KEY from your vault / .env")
    if not key.startswith(("sk_test_", "sk_live_")):
        return ("red", "STRIPE_API_KEY malformed", "should start with sk_test_ or sk_live_")
    return ("green", f"STRIPE_API_KEY set ({key[:8]}…)", None)
```

When a vault pack lands, integrations swap `os.environ[...]` for a
provider lookup in a ~5-line patch per pack.

## File reservations for concurrent workers

When two or more PO flows run against the same rig, same-file edits
collide. PO does **not** ship a reservation primitive — per principle
§5, we compose with `mcp-agent-mail` which already has
`file_reservation_paths`, `renew_file_reservations`,
`release_file_reservations`, and `force_release_file_reservation`.

**Convention.** Every prompt that edits files:

1. **Registers its identity ONCE at role entry** (mcp-agent-mail
   requires this before any reservation/mail call):
   - `ensure_project project_path="$PWD"` → returns `project_key`
   - `register_agent project_key=<above> name="{{issue_id}}-{{role}}" program="claude-code" model="opus-4"` —
     "already exists" is fine; idempotent.
2. Reserves its intended path set via
   `mcp-agent-mail file_reservation_paths` with
   `agent_name="{{issue_id}}-{{role}}"`. Collisions are legible —
   "builder from polymer-dev-abc.3 holds these paths."
3. On denial, mails the holder (`mcp-agent-mail send_message` to
   the `<issue_id>-<role>` agent named in the conflict response)
   or backs off + retries up to 3× before failing the step.
4. Renews the reservation if the turn runs > 4 min
   (default TTL is 5 min).
5. Releases via `release_file_reservations` after commit.
6. On crash, TTL auto-expires — no manual cleanup.

**Naming.** `mcp-agent-mail` agent names cannot contain `:`. Use
`{{issue_id}}-{{role}}` (hyphen) — e.g. `polymer-dev-abc.3-builder`.

Reservations apply to `build.md`, `lint.md`, `ralph.md`, `docs.md`
in `po-formulas-software-dev`. Any new file-editing prompt in any
pack should adopt the same convention.

**Optional escalation — precommit guard.** `mcp-agent-mail ships
install_precommit_guard` which refuses `git commit` on paths not
reserved by the current agent. Strong protection, operational
cost. Don't enable by default. Opt-in per rig if collisions keep
slipping through — e.g., `po doctor` check warns when it's missing
on a rig that has active parallel PO flows.

**Non-goals.** Don't build a PO Protocol wrapping reservations.
Don't switch agent-to-agent messaging to `mcp-agent-mail` — beads-
as-mail is fine for that. `mcp-agent-mail`'s file-reservation
tools are the specific pain it solves best; consume via MCP tool
calls from prompts.

## Lifecycle

```bash
# install — agent/human never learns uv
po packs install po-stripe                    # from PyPI
po packs install git+https://github.com/…/po-stripe@main
po packs install --editable /path/to/po-stripe

# inspect
po packs list                           # list installed + what each contributes
po list                                 # all registered formulas + commands
po show <name>                          # signature + docstring for a formula or command

# uninstall
po packs uninstall po-stripe
```

After install, the pack's entry points are live, its skills are
available to agents in any rig you run from, and its Python deps are
importable.

## When to make a new pack (vs extending an existing one)

Make a new pack when:

- The thing maps to a **distinct external system** (`po-stripe`,
  `po-gmail`, `po-gcal`)
- The thing is a **domain competency** distinct from shipping packs
  (`po-formulas-intake`, `po-formulas-ops`, `po-formulas-retro`)
- A group of users would install this subset together without the
  other stuff

Extend an existing pack when:

- You're adding another flow/command/check to an already-owned domain
- The new skill is a refinement of an existing one

Err on the side of more, smaller packs. Installing five small packs
is cheaper than auditing and uninstalling parts of one big one.

## Per-agent secrets

Roles often need real account credentials — `acquisitions-bot` posts
to Slack as itself, `triager` reads its own Gmail, etc. Core ships a
small `SecretProvider` Protocol so the orchestrator can inject ONLY
the current role's tokens into its tmux/CLI session, scrubbing every
peer-role scoped variable from the child env first.

### Naming convention

Secrets are keyed `<PREFIX>_<ROLE_KEY>` in the orchestrator's env (or
in a per-rig `.env` overlay). `<PREFIX>` is the unscoped name the
agent's tooling expects (e.g. `SLACK_TOKEN`); `<ROLE_KEY>` is the
role name normalized via `prefect_orchestration.secrets.role_env_key`:

| Role                              | `<ROLE_KEY>`                       |
|-----------------------------------|------------------------------------|
| `planner`                         | `PLANNER`                          |
| `plan-critic`                     | `PLAN_CRITIC`                      |
| `acquisitions.bot`                | `ACQUISITIONS_BOT`                 |
| `prefect-orchestration-4ja.1`     | `PREFECT_ORCHESTRATION_4JA_1`      |

Rule: hyphens, dots, spaces and any non-alphanumeric run collapse to a
single `_`; result is uppercased. Symmetric — docs and lookup share
the same normalizer so `.env` keys never silently miss.

### Default prefixes

```
SLACK_TOKEN, GMAIL_CREDS, ATTIO_TOKEN, CALENDAR_CREDS
```

Pass `prefixes=(...)` to any provider to extend (e.g. `STRIPE_KEY`).
Whatever the prefix, the child sees the bare prefix as the key:
`SLACK_TOKEN_PLANNER=xoxb-…` becomes `SLACK_TOKEN=xoxb-…` for the
planner's session.

### Per-rig `.env` overlay

A rig can ship a `.env` file with role-scoped tokens. Core does NOT
auto-load it — the caller (registry factory or `build_registry`) opts
in:

```python
from prefect_orchestration import (
    ChainSecretProvider, DotenvSecretProvider, EnvSecretProvider,
)
provider = ChainSecretProvider([
    DotenvSecretProvider(rig_path / ".env"),
    EnvSecretProvider(),
])
session = AgentSession(role="planner", repo_path=rig_path,
                      secret_provider=provider, ...)
```

### Precedence

`ChainSecretProvider` is **first-hit-wins per key** across providers.
Recommended order: rig `.env` overlay first, process env second.
Anything more bespoke (CLI flag, vault) goes in front of both as its
own provider. Vault, OAuth refresh, and rotation are explicitly out
of scope for the initial seam — the Protocol is forward-compatible.

### Example `.env`

```
# Per-role Slack tokens (one bot user per role)
SLACK_TOKEN_ACQUISITIONS_BOT=xoxb-acq-...
SLACK_TOKEN_TRIAGER=xoxb-tri-...

# Gmail creds (JSON-encoded, single line)
GMAIL_CREDS_ACQUISITIONS_BOT={"client_id":"...","refresh_token":"..."}
```

### Leakage scrub

Every `SessionBackend` calls `_clean_env(extra_env)` which (a) strips
`ANTHROPIC_API_KEY`, (b) removes every `<PREFIX>_*` key from the
orchestrator's env, then (c) overlays the role's re-keyed subset. Net
effect: role A's child process sees `SLACK_TOKEN=<A's token>` and
zero `SLACK_TOKEN_*` vars. Role B's token is not present at any step
of role A's launch.

## Two non-Python escape hatches (future)

Not shipping yet, but worth naming:

1. **Pure-directory packs** (GC-style) — a directory with
   `pack.toml` but no `pyproject.toml`. Installs via `po install-dir
   <path>` without pip. Good for content-only packs (skill + shell
   command). Not built today.
2. **Plugin-source packs** — a pack that's also a Claude Code plugin,
   so its skills load via Claude's plugin discovery in addition to
   overlay. Convenient for globally-available skills.

Defer both until a concrete use case appears.

## Related

- Principles: `principles.md` §3 (pack lifecycle), §4 (two dispatch
  verbs), §5 (compose before inventing).
- Separation: `separation.md` — core vs pack boundary, starter
  meta-pack, build-next order.
- Mechanics of overlay + skills merging: issue `4ja.4` (in-flight).
- Reference pack (first concrete tool pack): `po-stripe` — issue
  filed separately.
