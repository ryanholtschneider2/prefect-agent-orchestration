# Formula modes — `PO_FORMULA_MODE`

`software_dev_full` ships TWO bodies behind one entry point:

- **`legacy`** (default): the 305-line `@flow` with five nested
  Python loops (`plan-critic ⟲`, `build-critic ⟲` with `regression_gate`
  retry, `verify ⟲`, `ralph ⟲`, `full_test_gate` fix-up ⟲). This is
  the unmodified pre-7vs.5 body, preserved verbatim for backwards
  compatibility.
- **`graph`**: a thin seed-bead author + bounded watcher loop around
  `graph_run`. Loops live in the bead graph itself — critic agents
  extend the graph at runtime by `bd create`-ing iter+1 beads on
  rejection, and `graph_run` rediscovers the new sub-graph on the
  next pass.

Toggle:

```bash
# Default behavior (legacy):
po run software-dev-full --issue-id <id> --rig <name> --rig-path <path>

# Graph mode:
PO_FORMULA_MODE=graph po run software-dev-full \
  --issue-id <id> --rig <name> --rig-path <path>
```

Per-rig opt-in via `<rig>/.po-env`:

```
PO_FORMULA_MODE=graph
```

## Why two modes

7vs.5 implements the **graph mode**; 7vs.6 will delete the legacy
body once graph mode has dogfooded for ~1 month at default-on. The
flag exists to stage the migration safely:

1. Ship code with `legacy` as default — no user-visible behavior change.
2. Dogfood `graph` on `7vs.5.demo` (a hand-crafted trivial issue).
3. After 3+ green graph-mode runs, flip the default in a separate
   issue.
4. After 1 month at default-graph, delete legacy in 7vs.6.

## Architecture (graph mode)

```
software_dev_full(graph)
  └── seed_initial_graph(<issue>) drops 19 role-step beads under <issue>
  └── for pass in 1.._MAX_PASSES:
        _enforce_caps()           # close iter beads exceeding cap
        graph_run(root=<issue>,   # dispatch the OPEN frontier
                  formula="per-role-step")
        if submitted == 0: break  # steady state
```

Each role-step bead has the shape:

- `<issue>.role.<role>` — one-shot roles (triager, deploy-smoke, …)
- `<issue>.role.<role>.iter<N>` — iterating roles (builder-plan,
  plan-critic, builder, build-critic, linter, tester-*, verifier,
  cleaner, tester-full-gate)

`per_role_step` (a separate `po.formulas` entry point, dispatched by
`graph_run`, not by humans) parses the role from the bead id, looks
up the matching `@task` in `ROLE_TASKS`, and runs it.

## Critic-driven graph extension

When a critic closes its iter bead with `rejected: …`, the critic's
prompt instructs it to ALSO `bd create` the next iter's beads with
`--deps blocks:<this-bead>` so the dispatcher picks them up next
pass. See:

- `software-dev/po-formulas/po_formulas/agents/plan-critic/prompt.md`
  (`# REJECTED PATH — graph mode`)
- `software-dev/po-formulas/po_formulas/agents/build-critic/prompt.md`
  (same section, build-loop fan-out)
- `software-dev/po-formulas/po_formulas/agents/verifier/prompt.md`
  (verifier rejection re-creates the entire build loop at iter+1)

The orchestrator never mints iter+1 in graph mode. The orchestrator
DOES mint cap-exhaustion closes on iter beads whose `iter<N>` exceeds
their cap — `bd close` of an already-closed bead is idempotent so
this is safe to run as a pre-pass no-op.

## Cap enforcement

`_enforce_caps` runs BEFORE each `graph_run` pass:

1. Snapshot the open frontier via `list_subgraph(include_closed=False)`.
2. For each open iter bead, parse `(role, iter_n)` from its id.
3. If `iter_n > caps[role]`, close with `cap-exhausted: <cap_name>=<N>`.

Closing the bead removes it from the next pass's frontier (because
`include_closed=False` filters it out). This makes the cap-policy
**a property of the graph**, not a property of orchestrator scope.

## `_MAX_PASSES` safety belt

`_MAX_PASSES = 12` is a safety stop, not the primary stop condition.
The real termination is `submitted == 0` (steady state). Sum of all
default caps is `2+3+3+3+2 = 13`; `_MAX_PASSES = 12` is intentionally
below that so genuinely runaway loops fail loud rather than burning
budget. If `_MAX_PASSES` is hit in a real run, something is wrong —
investigate the bead graph in the run dir's `bd dep tree <issue>`
output.

## Decision log

Per-issue rationale lives at:

`<rig>/.planning/prefect-orchestration-7vs.5/prefect-orchestration-7vs.5-decision-log.md`

It captures the nine hard design questions the plan settled (who
creates iter+1 beads, reactive dispatch mechanism, seed bead
structure, cap-exhaustion policy, inter-step context handoff,
role-session affinity, dynamic graph extension, `PO_FORMULA_MODE`
plumbing) and any deviations during implementation.

## See also

- `engdocs/principles.md` §1, §5 — CLI vs Python, compose-before-
  inventing.
- `engdocs/separation.md` — formula authorship in packs, not core.
- `engdocs/minimal-task.md` — precedent for a small flow with
  bead-mediated handoff.
- `software-dev/po-formulas/po_formulas/seed_graph.py` — seed-bead
  authorship.
- `software-dev/po-formulas/po_formulas/per_role_step.py` — node-level
  formula contract.
