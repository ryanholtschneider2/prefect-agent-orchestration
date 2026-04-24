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
│   └── agents/<role>/prompt.md        per-role prompts for flow steps (4ja.3)
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

The overlay (`4ja.4`) is for files the agent's working directory
should contain when a session starts. Typical contents:

- `overlay/CLAUDE.md` — pack-specific agent instructions that
  reinforce the skill (repetition is fine — prompts are cheap)
- `overlay/scripts/` — helper shell scripts the agent can call
- `overlay/.env.example` — document required env vars (not real secrets)
- `overlay/prompts/` — rendered prompt templates the agent might include

Overlay does NOT contain secrets, rig-specific config, or anything
that should survive session end. It's pack-authored, pack-versioned,
idempotent to copy.

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

## Lifecycle

```bash
# install — agent/human never learns uv
po install po-stripe                    # from PyPI
po install git+https://github.com/…/po-stripe@main
po install --editable /path/to/po-stripe

# inspect
po packs                                # list installed + what each contributes
po list                                 # all registered formulas + commands
po show <name>                          # signature + docstring for a formula or command

# uninstall
po uninstall po-stripe
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
