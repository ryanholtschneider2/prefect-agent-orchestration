# Core / pack / nanocorp separation

A **nanocorp is a deployment**, not a package. It's the set of packs
installed + the config + the rigs it operates against. There is no
single "nanocorp pack" — there are **domain packs** that a nanocorp
assembles.

## 1 — Current layout (physical)

```
nanocorps/
├── prefect-orchestration/            ← core, no domain logic
│   ├── prefect_orchestration/        (agent_session, cli, deployments,
│   │                                  doctor, templates, telemetry, …)
│   └── engdocs/, CLAUDE.md, ...
│
├── software-dev/
│   └── po-formulas/                  ← first-party pack: software-dev
│       └── po_formulas/              (software_dev, epic, mail,
│                                      deployments, agents/*/prompt.md)
│
└── (future)                          ← additional packs, siblings of
                                       software-dev/
```

The core package has been kept domain-free through all the work so
far. Every concrete formula, prompt, or agent-role definition lives
in `software-dev/po-formulas/` or a future sibling pack.

## 2 — The line: what's core vs pack

| In core (`prefect-orchestration`) | In a pack |
|---|---|
| `AgentSession`, `SessionBackend` Protocol, all backends | concrete `@flow` composition of sessions |
| `po` CLI verbs (`list`, `run`, `show`, `deploy`, `install`, `packs`, `doctor`, `logs`, `artifacts`, `sessions`, `retry`, `watch`) | entry-point registrations that feed those verbs |
| Entry-point groups: `po.formulas`, `po.deployments`, `po.commands`, `po.doctor_checks`, `po.integrations` | registrations within those groups |
| `BeadsStore`, `FileStore`, `MetadataStore` Protocol | consumers of the store |
| Prompt rendering (`{{var}}` substitution) | actual `.md` prompts |
| `read_verdict` / `write_verdict` artifact convention | agent-authored verdicts |
| Telemetry primitives (OTel spans — once `9cn` lands) | span labels applied by pack flows |
| `@require_human_approval`, `@budget` decorators (when they land) | applications of those decorators in flow definitions |
| Protocols for future primitives (memory store, vault, …) | concrete implementations |

**Rule:** if the thing is hard to describe without naming a domain
(`software-dev`, `seam-recruiting`, `Stripe`, `Gmail`, `weekly-retro`),
it belongs in a pack. If it's a *kind* that every pack will instantiate,
it belongs in core.

## 3 — Candidate domain packs

Mapped against the primitives-doc gaps (`engdocs/primitives.md §1`).
Domain-scoped packs, each independently installable. A nanocorp picks
the subset it needs.

### Software-dev (shipping today)
`po-formulas-software-dev` — actor-critic software pipeline + epic
fan-out + mail helper. Covers formula-class work for "ship code."

### Integrations packs (the big platform lever — `primitives §3`)
One pack per external system, all registering `po.integrations`:

| Pack | Owns | Provides |
|---|---|---|
| `po-integrations-stripe` | payments | `StripeClient`, `charge()`, idempotency, webhook parsing |
| `po-integrations-gmail` | email | `GmailClient`, `send()`, `list()`, `fetch()`, MIME builders |
| `po-integrations-gcal` | calendar | create/list events, free-busy, meeting invites |
| `po-integrations-slack` | messaging | `post()`, `upload()`, DMs, channels |
| `po-integrations-attio` (or `-hubspot`, `-linear`, …) | CRM | typed record CRUD, dedup helpers |
| `po-integrations-github` | SCM ops | branch, PR, review fetch (complements `gh` CLI) |

Each integration pack ships: typed client + auth loader chain + idempotency wrapper + audit hooks + prompt fragments + budget-gate hook.

### Operations packs (domain flows)
One pack per operational competency. These are the "nanocorp-specific" stuff but named by function:

| Pack | Owns | Example formulas |
|---|---|---|
| `po-formulas-intake` | receiving + triaging inbound | `triage-inbox` (Gmail → classify → route), `website-form-to-bead`, `cold-outreach-dedupe` |
| `po-formulas-ops` | back-office operations | `invoice-reconcile`, `vendor-payment-approve`, `weekly-bookkeeping`, `calendar-audit` |
| `po-formulas-retro` | org-level reflection + planning | `weekly-kpi-digest`, `update-prompts-from-lessons` (the feedback loop), `quarterly-plan-generate` |
| `po-formulas-growth` | outreach + content | `linkedin-dm-draft`, `content-calendar-plan`, `seo-audit-run` |

Not every nanocorp needs all four. A recruiting-focused corp might run `intake` + `ops` + a domain-specific `po-formulas-recruiting`. A content-focused one might run `growth` heavily and `ops` sparingly.

### Primitive-implementation packs (as needs appear)
| Pack | When to build | Notes |
|---|---|---|
| `po-memory-<provider>` (Mem0, Letta, or stdlib vector file) | Once `update-prompts-from-lessons` runs regularly and you want semantic recall, not just recency | Ships a `MemoryStore` Protocol impl |
| `po-vault-<provider>` (1Password, HashiCorp, GCP SM, env-vars) | Once installed integration-pack count hits 3+ and env-vars start hurting | Ships a `CredentialProvider` Protocol impl |
| `po-policy` | Once approval + budget rules hit ~10 total | Ships `@require_human_approval`, `@budget`, and a tiny rule registry. Could also live in core; defer to a pack if it stays simple. |

## 4 — What the `seam-recruiting` rig gets us

`seam-recruiting` is a **rig** (git repo), not a pack. The PO pack
that drives it is `po-formulas-software-dev`. If seam-recruiting ends
up needing recruiting-specific flows (sourcing → outreach → screen
→ handoff), those land in a new pack — call it
`po-formulas-recruiting` — installed alongside software-dev. The rig
stays the code; the pack stays the flow definitions.

This is the generalization: **a nanocorp `=` N rigs + M installed
packs + C configured deployments + a Prefect server.** No one pack is
"the nanocorp."

## 5 — Build-next ordering with pack boundaries

Revised from `primitives.md §5`, tagged by what lives where:

| # | Feature | Where |
|---|---|---|
| 1 | OTel/Logfire spans (`9cn`) | **core** |
| 2 | `@require_human_approval` decorator | **core** (primitive) |
| 3 | `@budget(daily_cap_usd=X)` decorator | **core** (primitive; reads OTel from 1) |
| 4 | Pack lifecycle CLI (`po install/update/packs`) (`4ja.1`) | **core** |
| 5 | `po.integrations` entry-point group + first integration pack | **core** declares the group; first impl in a **new pack** (`po-integrations-stripe` is my pick — highest cost-of-mistake = most value from gating) |
| 6 | 2-3 more integration packs (gmail, gcal, slack) | **new packs** |
| 7 | `update-prompts-from-lessons` formula | **new pack**: `po-formulas-retro` — first formula justifies the pack |
| 8 | Credential vault adapter | **core** (Protocol) + **new pack** (first impl) |
| 9 | Domain flows as needed (`triage-inbox`, `invoice-reconcile`, …) | **new packs** `po-formulas-intake` / `-ops` |

The in-flight parity epic (`4ja`) delivers (4) and the `po.commands` /
`po.doctor_checks` scaffolding (5 partially). Once that lands we can
start (5) in earnest.

## 6 — Naming convention for future packs

```
po-formulas-<domain>           # shipped flows for a domain (code-shipping, recruiting, intake, ops, retro, growth)
po-integrations-<system>       # typed client + auth + idempotency for one external system
po-<capability>-<provider>     # implementations of a core Protocol (memory, vault)
```

All use entry points declared by core. All live as sibling directories
under `nanocorps/`. Users install via `po install <pack>` (landing
with `4ja.1`). Agents never learn `uv` / `pip`.

## 7 — What does NOT go in any pack

Per `engdocs/primitives.md §6` non-goals, with pack-specific rephrasing:

- **Don't** build a pack that reinvents `Linear` / `Stripe` / `Gmail`
  as the source of truth. Integration packs *connect to* external
  systems; they don't replace them.
- **Don't** ship a pack that exposes NATS-style event bus primitives.
  If a use case emerges, the integration pack for the real event bus
  (NATS / Kafka / SQS) is where it belongs.
- **Don't** build a `po-formulas-nanocorp` monolith. Break it by
  function so users install only what they need and so the concerns
  stay testable.
- **Don't** couple core to any concrete integration. Core exposes
  entry-point groups and Protocols; packs fill them in.

## 8 — Open questions worth revisiting later

- Should **`@require_human_approval`** and **`@budget`** live in core or
  a `po-policy` pack? Today: put them in core as decorators, since
  they're primitive plumbing. If rule surface grows past ~20 rules
  across different dimensions, extract to a pack.
- Is **memory** core or pack? Probably a Protocol in core + pack
  implementations. Mirror the `SessionBackend` / `MetadataStore` shape.
- Does **reputation** need its own pack, or is it a `po-formulas-retro`
  side-effect that updates bead tags per (role, formula)? Start with
  the latter.
- Where do **per-pack CLAUDE.md fragments** live? Each pack's README +
  its own CLAUDE.md, with a root-level index in the nanocorp's rig.
