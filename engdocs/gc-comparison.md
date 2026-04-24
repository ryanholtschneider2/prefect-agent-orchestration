# Gas City vs. PO — feature-level comparison

Head-to-head comparison between **Gas City** (`gc` — Go-based agent
orchestration SDK) and **PO** (`po` — this repo, Python/Prefect-based).
Not a pick-a-winner exercise; not measured against an ideal. Just:
*what does GC do, what does PO do, and what's worth lifting into PO to
reach functional parity where it makes sense?*

Gas City and PO are not going to be co-deployed. This is design prior,
not integration planning.

## 1 — GC's intent (in one paragraph)

Gas City is a **config-driven agent orchestrator** for "autonomous
organizations of agents that also take human input." Zero hardcoded
roles in the Go core — `Mayor`, `Polecat`, etc. are pack-level TOML
conventions. The `gc` CLI + long-running controller own lifecycle
(start/stop/reconcile agents), dispatch (`gc sling <agent> <bead>`),
messaging (mail, nudges), formulas (workflows as TOML recipes), and
orders (scheduled/event-driven triggers). Beads is the source of truth
for work state; events land in an append-only `.gc/events.jsonl`.
Agents are long-lived tmux/k8s/subprocess sessions, supervised in
OTP/Erlang style (controller as supervisor, agent as worker, crash =
restart + quarantine).

## 2 — Concept-by-concept comparison

| # | GC concept | What GC provides | What PO provides | Gap (if any) | Worth lifting? |
|---|---|---|---|---|---|
| 1 | **Agent runtime** | `agent.Protocol` with tmux / k8s / subprocess / ACP providers; long-lived sessions; fingerprint drift detection; crash-loop quarantine | `AgentSession` + `SessionBackend` protocol; `ClaudeCliBackend`, `TmuxClaudeBackend`, `StubBackend`; per-turn subprocess; no long-running agents | PO doesn't need drift detection because every turn is a fresh subprocess. Pluggability is equivalent. | **No.** Different execution model; PO's is intentionally simpler. |
| 2 | **Task store (beads)** | `BdStore` / `FileStore` / `MemStore` / `exec.Store`. Everything is a bead: tasks, mail, sessions, molecules, convoys. | `BeadsStore` / `FileStore`. Tasks + mail (5kj) + run metadata in bead fields (logs). Convoys, sessions-as-beads, molecule-as-bead-tree not modeled. | We treat Prefect flow runs as the "molecule" rather than materializing per-step beads. Trade-off: humans don't see "step 7/16" in `bd`; they look at Prefect UI. | **Partial.** `bead-as-convoy` (grouping) is a cheap win for human scanning — see `po run graph` (`uc0`) which covers the same ground. Don't materialize per-step beads; the `$RUN_DIR` + Prefect UI is enough. |
| 3 | **Event bus** | `.gc/events.jsonl` — append-only, typed payload registry, 250ms poll watcher, best-effort record | None today. Prefect state transitions + Prefect Automations are the closest thing. | Meaningful gap *if* we want cross-system / cross-flow event-driven behavior. Today we don't. | **Defer.** Prefect has automations (event → flow run). Build `po` affordances on top only when a concrete use case emerges. |
| 4 | **Config system** | `pack.toml` + `city.toml` + `agent.toml` + per-rig patches. PackV2 imports with version pinning + provenance. Progressive activation (levels 0-8). | Python entry points (`po.formulas`, `po.deployments`). `pip install` is composition. No per-rig overrides. | GC can say "in rig A the polecat uses opus, in rig B it uses haiku" via `[rigs.patches]`. PO forces forking or a new pack. | **Worth lifting eventually.** See §3 below. Not urgent. Would be a minimal `po.toml` with `[rig.patches]` for model/prompts/formula-kwargs. |
| 5 | **Prompt templates** | Go `text/template`; `.template.md` opt-in; shared `shared/` partials auto-load; `{{template "name"}}` inline + `append_fragments` / `inject_fragments` for defaults | Python `{{var}}` substitution (dumb regex). One file per step. No partials, no auto-compose. | Duplication across prompts grows linearly with roles × formulas. Critic and reviewer prompts share big chunks of rubric already. | **Yes, soon.** Swap to Jinja2 (or stdlib `string.Template` + manual include) and auto-load a `shared/` dir. ~1 day of work. Small PR. |
| 6 | **Messaging (mail + nudge)** | Mail = message bead. Nudge = tmux input. Hooks auto-inject unread mail into agent prompts per turn. | `po_formulas.mail` (beads-as-mail, shipped). No tmux nudge (not needed — per-turn model). No auto-inject of unread mail in prompts. | Today the builder/critic prompts *reference* mail ("check your inbox") but nothing programmatically prepends it. | **Yes.** Small: wrap every `AgentSession.prompt()` to prepend unread-mail context for that role. ~1 hour. |
| 7 | **Formulas, molecules, orders** | Formula = TOML recipe. Molecule = materialized bead tree (steps as beads). Wisp = ephemeral molecule. Order = formula + trigger (cron/cooldown/event/condition/manual). | Formula = Python `@flow`. Order = Prefect deployment (cron/interval/manual, shipped via `po deploy`). No "molecule" — steps are Prefect tasks, not beads. Event + condition triggers not in `po deploy` yet (Prefect Automations UI-only). | PO matches GC for cron/interval/manual. Missing: event + condition triggers via `po deploy`. Missing: per-step-as-bead materialization (intentional). | **Partial.** Expose Prefect Automations via a thin `po automation` verb when an event-triggered use case appears. Skip molecule-as-bead. |
| 8 | **Dispatch (sling)** | `gc sling <agent> <bead>` — routes work via `work_query` on bead metadata (pool agents match `gc.routed_to=<name>`); auto-wraps in convoy; optional nudge. | `po run <formula> --issue-id X --rig-path ...` — caller picks formula + rig explicitly. No implicit agent routing. | PO requires the caller to know which formula to run. GC matches bead-to-agent automatically. | **No.** PO's explicit `po run <formula>` is simpler and matches principle §2 (CLI-first). Adding implicit routing would be a big surface-area expansion. |
| 9 | **Health patrol** | Controller `reconcileAgents` loop: fingerprint-drift → restart, crash-loop quarantine (sliding window), dep-ordered start/stop waves. OTP-style. | None. Prefect retries + state transitions cover the flow-level equivalent. No process-level supervision because no long-running agents. | Intentional absence — PO's "one subprocess per turn" model doesn't have daemons to supervise. | **No.** |

## 3 — Directory-layout features (the underrated stuff)

GC packs ship several directory conventions PO doesn't have analogues
for. Four of these are low-effort, high-value additions:

| GC directory | Purpose | PO analogue | Worth lifting? |
|---|---|---|---|
| **`commands/<name>/{help.md,run.sh}`** | User-facing ops as versioned pack content (e.g. "prepare the branch for review"). Runs via `gc <command>`. | Users write Slack messages or stash shell aliases. | **Yes — high-value.** Add a `po.commands` entry-point group. Packs ship `[project.entry-points."po.commands"]` entries pointing at Python callables or shell scripts. `po <name>` dispatches. Turns repeat human work into reviewable PRs. |
| **`doctor/<name>/run.sh`** | Pack-shipped health checks (beyond core's `po doctor`). | `po doctor` today only runs core checks. | **Yes — small.** `po.doctor_checks` entry-point group; each pack registers checks; `po doctor` runs all. |
| **`overlay/`** | Files copied into the agent's workdir at session start. | Not modeled. `AgentSession` just takes `cwd=<rig_path>`. | **Worth having for tmux backend.** When lurking, sometimes you want a standard `CLAUDE.md` or scratch dir present. Cheap: add an `overlay: Path \| None` to `AgentSession`. |
| **`template-fragments/`** | Reusable prompt partials (referenced via `{{template "x"}}` or listed in `append_fragments`). | Per-pack `prompts/` directory, flat. | **Yes — same work as §2.5.** When we switch to Jinja2, add a `shared/` or `fragments/` dir that auto-loads. |
| **Progressive activation (levels 0-8)** | Sections activate capabilities: only `[workspace]`+`[[agent]]` = single-agent mode; add `[daemon]` → task loop; etc. | Pack + entry points. Always "on." | **No — we're already minimal.** PO doesn't have levels because it doesn't have a supervisor to turn on. |

## 4 — Topology patterns (GC ships three; PO ships one)

GC's examples directory has three reference topologies:

1. **Gastown** — hierarchical, worktree-isolated. City-scoped Mayor/Deacon + rig-scoped worker pool. Formulas. Closest to PO's `software-dev-full` + `epic` pattern.
2. **Swarm** — flat peer, shared-directory. N coders + a committer. No formulas, no witness. Agents self-organize via mail + beads.
3. **Hyperscale** — k8s burst. Single worker template, `min=0` / `max=100`, prebaked container image, per-pod resources. For embarrassingly parallel work.

PO has the Gastown-equivalent in `software-dev-full` + `epic`. Swarm and
hyperscale are not implemented — and we don't have an immediate need.

**Worth lifting?** Swarm: no — the peer-coordination pattern is mostly a
policy choice (peer vs dispatcher) and PO's DAG handles the dispatcher
side well. Hyperscale: yes-ish, but it's primarily a Prefect work-pool
config (`--type kubernetes` with a job template) plus a prebaked image
build step. No PO code change required — just documentation. Worth
writing a `engdocs/topologies.md` when someone asks for it.

## 5 — What to lift into PO (ordered, cheapest first)

| # | Feature | Source (GC concept) | Effort | Value |
|---|---|---|---|---|
| 1 | **Unread-mail auto-inject** per-turn wrapper in `AgentSession.prompt()` | §2.6 messaging | ~1 hour | Closes the gap where prompts say "check inbox" but nothing enforces it |
| 2 | **`po.commands` entry-point group** — packs ship user-facing ops; `po <command>` dispatches | §3 `commands/` | ~1 day | Biggest low-effort win. Turns tribal shell knowledge into versioned pack content. |
| 3 | **`po.doctor_checks` entry-point group** — packs extend `po doctor` | §3 `doctor/` | ~2 hours | Pack authors declare health checks; core aggregates. |
| 4 | **Jinja2 + `shared/` fragments** in `templates.render_template()` | §2.5 prompts | ~1 day | Removes prompt duplication. Shared rubric for critic + reviewer. |
| 5 | **`overlay/` dir** copied into agent `cwd` at session start | §3 `overlay/` | ~2 hours | Small, nice-to-have for tmux lurking + standardized context files. |
| 6 | **`po.toml` with `[rig.patches]`** — per-rig overrides of formula kwargs (model, iter caps, prompts) | §2.4 config | ~2 days | Lets a single pack serve dev/staging/prod without forking. Defer until a second rig asks. |
| 7 | **Event + condition deployment triggers** via `po deploy` (thin wrapper over Prefect Automations) | §2.7 orders | ~1 day | Matches GC's order trigger set. Defer until we have a concrete event-driven formula. |

All 7 pass principles §1 + §2 (CLI-first, composes things Prefect
doesn't know about). Items 1-5 are clear wins. 6-7 are "build when
needed."

## 6 — What to explicitly *not* lift

- **Controller reconcile loop / OTP supervision.** PO's per-turn
  subprocess model makes this irrelevant.
- **Implicit bead-to-agent routing via `work_query`.** Violates
  principle §2 — makes the CLI less explicit.
- **Fingerprint drift detection.** Same reason.
- **Per-step bead materialization (molecules).** Prefect UI covers the
  visibility need without the bd write-storm.
- **Progressive config activation (levels 0-8).** PO is already minimal
  (pack + entry points). No latent supervisor to activate.
- **Go `text/template` / TOML DSL.** Python entry points + Jinja2 are
  more ergonomic for Python-shop users, and principle §1-compatible
  (no ritual overhead for the simple case).

## 7 — Filing plan

Of §5's 7 items, file `1`, `2`, `3`, `4`, `5` as beads issues now.
Leave `6`, `7` unfiled — they're "when a use case forces it."

- `po-mail-auto-inject` (P2, small)
- `po-commands-entry-point-group` (P2, medium)
- `po-doctor-checks-entry-point-group` (P3, small)
- `po-jinja2-shared-fragments` (P2, medium)
- `po-overlay-dir` (P3, small)

Together these would close the GC-parity gap for everything that
matters, without taking on the OTP supervisor or the TOML config
overhead.
