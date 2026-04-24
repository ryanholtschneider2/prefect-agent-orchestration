# Primitives for an AI-native organization

> **Superseded sections** (2026-04-24 design pass, see `principles.md §5`
> and `separation.md`): this doc's §3 ("integration-packs idea")
> and §5 items 4-8 proposed a `po.integrations` entry-point group with
> an `IntegrationSpec` wrapper per integration. That was dissolved —
> see `engdocs/pack-convention.md` for the current shape. In short:
> there is no framework, no typed clients in PO, no `IntegrationSpec`.
> A "tool pack" is a regular PO pack that ships a vendor SDK dep + a
> Claude Code skill + commands + doctor checks. Agents call the vendor
> SDK directly. Treat §3 and §5 below as historical context.


What's actually required, beyond an agent runtime, to run an org — human+AI
or AI-only — with governance, version control, orchestration, and
communication that works in practice. Snapshot of the landscape as of
2026-04-24, from the perspective of `po` (Prefect-orchestration) +
Claude Code as the baseline stack. Gas City is referenced only where its
design prior informed ours; we're not planning to run them together.

This doc is a durable answer to the question: *if the wave-2 verb
rollout (artifacts/sessions/retry/watch) completes and we have the
observability hooks, what should we build next to actually be an
organization instead of a coordination layer?*

## 1 — The primitive set

Each row: what the primitive does, what `po`+Claude Code cover today,
what's missing. External systems are noted where the answer is
"don't reinvent this — buy/integrate it."

### Execution & work definition

| # | Primitive | PO + Claude Code | Gap |
|---|---|---|---|
| 1 | **Agent runtime** — subprocess, tmux, k8s wrappers | `AgentSession` with `ClaudeCliBackend` / `TmuxClaudeBackend` / `StubBackend` | None substantive — pluggable by design |
| 2 | **Work ledger** | `bd` (beads) — graph of work with deps, claim/close, metadata, audit | None — the source of truth for what |
| 3 | **DAG orchestrator** | Prefect 3 with bead-deps → `wait_for=` | None — Prefect's core strength |
| 4 | **Dispatcher / pool** | Prefect work pools (`--type process/k8s/docker`) | None — `prefect worker start` owns this |
| 5 | **Formula registry** | `po.formulas` entry-point group | None — composable recipes are first-class |
| 6 | **Prompt templates + fragments** | `render_template()` + per-pack `prompts/` dir | Weak partials; no auto-composition. Worth revisiting if prompts grow. |

### Communication

| # | Primitive | PO + Claude Code | Gap |
|---|---|---|---|
| 7 | **Event bus** | — (no pub/sub; Prefect state transitions are the closest thing) | Add only if a use-case forces it. NATS / Kafka exist; we don't need our own. |
| 8 | **Mail** (agent↔agent, agent↔human, durable) | `po_formulas.mail` over `bd` (shipped) | Adequate for now; escalate to `mcp-agent-mail` if we need threads/acks |
| 9 | **Live lurk + steering** | `TmuxClaudeBackend` — `tmux attach -t po-<issue>-<role>` | Covers human-watches-agent. Human-chats-structured-with-flow is the missing piece (approval UI). |

### Scheduling

| # | Primitive | PO + Claude Code | Gap |
|---|---|---|---|
| 10 | **Cron / interval / manual** | `po deploy --apply` → Prefect deployments + `prefect worker start` | Adequate |
| 11 | **Event-triggered runs** | — (Prefect Automations is the path, UI-configured only today) | Could ship `po automation` as a thin wrapper once we feel the pain |

### Governance (the big gap, partially closed)

| # | Primitive | PO + Claude Code | Gap |
|---|---|---|---|
| 12 | **Approval gates** — "human signs off before X" | Partial: Claude `pre_tool_use` hooks can block per-tool. No flow-level signoff. | PO needs a `@require_human_approval(role="finance")` task/decorator. Models signoff as a bead transition (`bd update --approve=<human>`), un-blocks the flow. Thin layer over bd. |
| 13 | **Budget / cost gates** | Partial: Claude session tracks cost locally. Not enforced. | PO needs a `@budget(flow, daily_cap_usd=50)` decorator that reads OTel/Logfire spend-so-far and fails fast. Prerequisite: `9cn` (OTel spans) landed. |
| 14 | **Role-based access control** | Partial: Claude `--allowed-tools` / `--disallowed-tools` per session. | RBAC at the formula level — "which roles are authorized to run which formulas against which rigs" — is PO's to own. Tiny table lookup. |
| 15 | **Policy engine** | — | OPA / Cedar exist. For a small org, we probably don't need a general engine — a decorator + a table is enough. Escalate if policy rules exceed ~20. |

**Claude permissions are necessary but not sufficient.** They gate
*session behavior* (what tools, which paths). They do not gate
*org behavior* (aggregate spend, flow-level approvals, RBAC over
formulas). PO owns the delta.

### Observability & audit

| # | Primitive | PO + Claude Code | Gap |
|---|---|---|---|
| 16 | **Traces / logs / cost** | Prefect UI for flow runs; `9cn` (OTel/Logfire) open — adds per-prompt spans | Land `9cn`. Then we have cost-per-role, cost-per-formula, cost-per-issue. |
| 17 | **Run artifacts** | `<rig_path>/.planning/<formula>/<issue>/` with verdicts + critiques + reports | Adequate |
| 18 | **Reputation / track-record** | — | "Which builder writes the cleanest diffs" is not queryable. Likely a follow-up formula that reads from OTel + beads to compute per-(role, formula) scores over time. Not urgent. |

### Memory & knowledge

| # | Primitive | PO + Claude Code | Gap |
|---|---|---|---|
| 19 | **Cross-run memory** | Weak: per-run `lessons-learned.md` + `bd remember` per project | The user's insight: make this a **standing order**. A cron-triggered `update-prompts-from-lessons` formula reads recent lessons in a scope and appends/revises prompt fragments. Event-triggered on every Nth close also works. No new primitive. |
| 20 | **Institutional docs** | git + CLAUDE.md + engdocs/ (this file) | Adequate. Make it agent-queryable via `grep`/`semantic-search` MCP later. |

### External reality — "don't reinvent, ship defaults"

| # | Primitive | PO + Claude Code | Gap |
|---|---|---|---|
| 21 | **Integrations to real systems** — Stripe, Gmail, Calendar, CRM, Slack, banks, infra | — (agent's problem today) | **Biggest architectural win available.** Ship a `po.integrations` entry-point group with first-party default packs: `po-integrations-stripe`, `-gmail`, `-gcal`, `-slack`, `-attio`, `-linear`. Each pack provides typed client + auth loader + idempotency + audit hooks + prompt fragments describing its API. Formulas `from po_integrations.stripe import charge` and get all that for free. See §3 below. |
| 22 | **Credential vault** | — (env vars) | Land after `po.integrations` starts exceeding 3 providers — each integration pack gets a credential provider they can share. Keep plugin-shaped (vault / 1Password / env-vars / GCP SM). |
| 23 | **Org state machine** (KPIs, goals, pivots) | — | Not PO's to own. External: Linear/Notion/Stripe/Grafana. PO can expose a read-only `po kpi` verb that queries from those via integration packs. |

### Deployment / SDLC

| # | Primitive | PO + Claude Code | Gap |
|---|---|---|---|
| 24 | **Environment overlay** (dev/stg/prod) | — | Post-integrations, environment is just "which credential set + which rig-path the formula binds to." A config overlay in `city.toml`-equivalent would solve it. |
| 25 | **Formula / prompt versioning + A/B + rollback** | Partial: git. No runtime A/B. | Probably unnecessary until we have multiple parallel formulas doing the same thing and want to compare. |
| 26 | **Failure recovery / checkpointing** | Prefect retries + per-role session resume | Adequate |

## 2 — Minimum viable AI-native org

If you want to run a nanocorp that serves real customers, the
*minimum* primitives beyond the base execution layer are:

1. **Approval gates (12)** — so the org can sign contracts, send
   payments, publish content with a human in the loop when needed.
2. **Budget gates (13)** — so runaway agents can't set money on fire.
3. **Integration packs (21)** — so formulas interact with real systems
   (pay, email, schedule, update CRM) via vetted defaults.
4. **Credential vault (22)** — so (21) has somewhere to get secrets
   that isn't `.env`.
5. **Cross-run memory as a standing order (19)** — so the org learns.

Everything else (RBAC, policy engine, reputation scoring, org state
machine, A/B, env overlay) is a *scaling* concern — add when the org
grows past ~3 formulas, ~5 agents, or ~1 external customer.

## 3 — The integration-packs idea in concrete shape

New entry-point group:

```toml
# po-integrations-stripe/pyproject.toml
[project.entry-points."po.integrations"]
stripe = "po_integrations.stripe:register"
```

Each integration pack's `register()` returns an `IntegrationSpec`
carrying:

- **Typed client** (`StripeClient`) with auth loader chain
  (env → vault → 1Password → …).
- **Idempotency wrapper** keyed on an `issue_id` or `run_id` so retries
  don't double-charge.
- **Audit hooks**: every call emits a bead event / OTel span labeled
  with `integration`, `provider`, `operation`, `cost_usd` where
  applicable.
- **Prompt fragments**: `template-fragments/stripe.md` describing the
  available ops (shipped with the pack, auto-loaded by formulas that
  depend on it).
- **Budget gate hook**: reports real-dollar spend to the budget
  aggregator (`13`) so gates work correctly.

Formulas opt in:

```python
from po_integrations.stripe import charge

@task(tags=["integration:stripe"])
def process_payment(invoice_id: str) -> dict:
    return charge(amount=..., currency=..., idempotency_key=invoice_id)
```

Claude-facing prompt gets the auto-loaded fragment describing
`charge()`, so the builder writes idempotent invocations by default.

The same shape for `gmail` (send, list, fetch), `gcal` (create event,
list), `slack` (post, upload), `attio`/`hubspot`/`linear` (typed
record CRUD).

Default-ship ~6 packs; a nanocorp picks the subset they need. Users
can also ship their own — same entry-point contract.

## 4 — Feedback loop as a standing order (Ryan's refinement)

No new primitive. A formula `update-prompts-from-lessons`:

```python
@flow(name="update_prompts_from_lessons")
def update_prompts(scope: str = "software-dev-full", days: int = 7):
    lessons = collect_lessons_learned(scope=scope, since=timedelta(days=days))
    if not lessons:
        return {"status": "empty"}
    synthesized = summarize_and_diff(lessons)   # one Claude turn
    write_prompt_fragments(scope, synthesized)  # commits to pack repo
    return {"updated_fragments": [...]}
```

Registered as a Prefect deployment, scheduled weekly via `po deploy`.
Or event-triggered when `bd close` count in scope crosses a threshold.

This turns the per-run `lessons-learned.md` dump from a write-only log
into a feedback channel that actually feeds back.

## 5 — What PO should build next (ordered)

1. **Ship `9cn`** (OTel/Logfire) — unblocks budget gates and reputation scoring.
2. **`@require_human_approval`** task helper — models signoff as a
   bead transition. Small, PO-native, immediately useful.
3. **`@budget(daily_cap_usd=X)`** task/flow decorator — reads OTel cost
   totals, fails fast. Depends on (1).
4. **`po.integrations` entry-point group + 3 default packs** (stripe,
   gmail, linear) — the biggest platform-vs-framework lever we have.
5. **`update-prompts-from-lessons` formula + weekly deployment** — feedback
   loop closed, no new primitive, ~1 day of work.
6. **Credential vault adapter** — pluggable, starts with env-vars
   provider; adds vault/1Password as integration-pack count grows.

Everything past item 6 (RBAC policy engine, reputation scoring, env
overlay, A/B, org state machine) is scaling concern. Ship 1-6 first.

## Non-goals / don't build

- **A general-purpose event bus** — NATS exists; wait until a use case
  needs cross-system pub/sub.
- **A generic policy engine** — OPA/Cedar exist; we probably don't need
  more than 20 rules. A decorator + a Python table is enough.
- **Our own KPI dashboard** — Grafana / Metabase exist. `po kpi` can
  query them via integration packs.
- **Replacing Linear / Stripe / Gmail** as source of truth. Beads
  orchestrates the *workflow around* them; external systems remain
  authoritative for their own records.
