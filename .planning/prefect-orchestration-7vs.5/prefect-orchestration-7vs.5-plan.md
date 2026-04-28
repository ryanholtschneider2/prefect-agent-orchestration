# Implementation Plan: prefect-orchestration-7vs.5

Collapse `software_dev_full` to a seed-bead + `graph_run` handoff,
behind a `PO_FORMULA_MODE=legacy|graph` flag. Body must be <100 LOC
in graph mode. Scope B (full reactive graph) per user direction.

## Issue Summary

Today's `software_dev_full` is a 305-line Python `@flow` body containing
five dynamic loops (`plan-critic ⟲`, `build-critic ⟲` with
`regression_gate` retry, `verify ⟲`, `ralph ⟲`, `full_test_gate` fix-up
⟲). 7vs.3 piloted the verdict-as-bead pattern for lint; 7vs.4 extended
it to plan/build critique. 7vs.5 finishes the move: in `graph` mode the
formula body becomes a thin **seed-bead author** that drops a small
fixed sub-graph of role-step beads, then hands off to `graph_run` (the
existing edge-driven dispatcher) for execution. Loops become bead
graph extensions made by critic agents themselves.

## Research Summary

### Existing primitives we compose (per `engdocs/principles.md` §5)

- `prefect_orchestration.beads_meta.create_child_bead(parent, child_id,
  title, description, blocks=…, rig_path=…)` — already used by lint /
  plan-critic / build-critic to drop iter beads. (`software_dev.py:267`,
  `:366`, `:712`.)
- `beads_meta.watch(bead_ids, event="close", …)` — push-ish change
  feed over `bd show` polling (1.5 s default). Already shipped, doc'd
  to be upgradable to dolt change-feed without changing call shape.
- `beads_meta.list_subgraph` / `topo_sort_blocks` / `_bd_show` —
  graph collection + ordering, used by `graph_run` today.
- `po_formulas.graph.graph_run` — flow that resolves a formula by EP
  name, validates `(issue_id, rig, rig_path)` signature, fans
  topo-sorted nodes out as `wait_for=`-chained tasks, returns
  `{"results": {id: result, …}}`.
- `prefect_orchestration.role_registry.RoleRegistry` +
  `build_registry` — per-role Claude session map, seed-bead-keyed via
  `RoleSessionStore` so role→uuid persistence is safe across
  separate Python processes / Prefect tasks. `resolve_seed_bead`
  walks parent-child up; for our case the **input issue is the seed**
  and every `<issue>.<role>.iter<N>` bead resolves back to it.
- `parsing.read_verdict` / `prompt_for_verdict` and the
  `<run_dir>/verdicts/<step>.json` artifact — kept untouched (7vs.6
  is the deletion issue; this issue must not regress them).
- `_build_critic_iter_description` — the iter-bead description format
  is a shipped contract; preserved verbatim.

### What does NOT need new primitives

The user's proposed reactive-graph mechanism is fully composable from
the four already-shipped primitives above:

1. Seed flow drops the **initial fixed sub-graph** (one bead per role
   step + fixed `--blocks` edges).
2. `graph_run` dispatches each bead via a new `per_role_step` formula
   (1 EP, ~70 LOC pack-side) that runs the corresponding `@task` from
   today's `software_dev.py`, then exits. Bead is closed by the agent
   inside that task (existing convention).
3. Iteration extension: critic agents, in their existing prompts, are
   already creating iter+1 beads via `bd create`. We move bead creation
   responsibility from the orchestrator into the critic prompt for
   the rejected-path case, and have `graph_run` re-traverse to pick
   up the new beads — done with one watcher loop around `graph_run`.

This satisfies principles §5 promotion test items 1–4 negatively:
no new primitive earns its keep; we are **composing**.

### Engdocs alignment

- `engdocs/separation.md` §2: seed-bead authorship + role-step formula
  belong in **the pack**, not core. Core gets only one tiny addition
  (`beads_meta.watch_extensions` helper — see below) if needed; if
  composition with existing `watch()` is enough we add nothing to core.
- `engdocs/minimal-task.md` is the precedent: a small flow that
  dispatches role-tasks one-at-a-time with bead-mediated handoff.
  Graph-mode `software_dev_full` is "minimal-task with a fan-out at
  the verdict edge."
- `engdocs/primitives.md` row 17 (run artifacts): the
  `.planning/<formula>/<issue>/` convention is preserved; only
  authorship changes.

## Success Criteria

### Acceptance Criteria (from issue)

- (a) `PO_FORMULA_MODE=graph` runs full `software_dev_full` end-to-end
  on a real issue (i.e. produces the same final state: bead closed,
  run-dir populated, verdicts written).
- (b) `PO_FORMULA_MODE=legacy` (the default) still works, byte-for-byte
  equivalent to today.
- (c) `software_dev_full` body in graph mode is **<100 LOC**, comments
  and blank lines included (counted from the `@flow` decorator to its
  closing `return`, not counting helper defs above the decorator).
- (d) Documented in repo `CLAUDE.md` + `engdocs/`.

### Demo Output

```
$ PO_FORMULA_MODE=graph po run software-dev-full \
    --issue-id <id> --rig <name> --rig-path <path>
2026-04-NN ... [software_dev_full] mode=graph; seeding role-step beads
2026-04-NN ... [software_dev_full] dropped 7 seed beads + 3 fixed edges
2026-04-NN ... [graph_run] submitting 7 nodes ...
2026-04-NN ... [per_role_step] role=triager bead=<id>.role.triager
... (each role runs as its own task, visible in Prefect UI) ...
2026-04-NN ... [software_dev_full] graph_run pass 1 complete; 2 new iter beads observed
2026-04-NN ... [graph_run] submitting 2 nodes ...
... eventual termination, bd close <id>
```

Same `<rig>/.planning/software-dev-full/<id>/` artifact tree as today.
Bead graph (`bd dep tree <id>`) is the new value-add: every loop
iteration is a queryable bead.

## Implementation Details

### Decision Log Seed (the nine hard design questions)

1. **Who creates iter+1 bead.** **Critic agent.** When a critic closes
   `<issue>.<step>.iter<N>` with `--reason "rejected: …"`, its prompt
   instructs it to also `bd create <issue>.<step>.iter<N+1>` with
   `--blocks <prior-iter-bead>` and the description constructed via
   the same `_build_critic_iter_description` template (we ship that
   logic into the prompt as a literal example block — duplication is
   fine per `principles.md §"Prompt authoring convention"`). The
   orchestrator only mints iter+1 in the **cap-exhaustion** branch
   (so a `bd close --reason "cap-exhausted: …"` is still on the
   prior iter bead) and only in legacy mode. In graph mode, cap is
   enforced via the seed flow's watcher cap counter (see #5).
2. **Reactive dispatch.** **Watcher loop around `graph_run`.** Seed
   flow does: `graph_run` once → on completion, snapshot bead
   children of the issue → if any new open beads appeared (created
   by critic agents during their tasks), call `graph_run` again
   over just the new sub-graph. Repeat until either no new beads
   appear in two consecutive passes (steady state) **or** the
   per-step iteration cap is hit. Polling cadence is 0 (we sample
   only at pass boundaries) — `beads_meta.watch()` is **not** used
   in this issue; we just snapshot via `list_subgraph` between
   passes. (`watch()` upgrade is a follow-up — see Deferred.)
   - Tradeoffs: poll-at-pass-boundary is the dumbest possible
     mechanism — no event subscriber, no threads, no race with
     concurrent agent edits during a pass. Worst case is a critic
     creates an iter+1 bead and we don't dispatch it until the
     current pass settles, which is exactly the semantic we want
     (don't dispatch a bead whose blocker hasn't closed yet).
   - Why not sub-flow-per-step: each Prefect sub-flow has ~3 s
     overhead and burns a flow-run row in the UI. 7-role pipeline
     × 3 build iters × 3 verify iters = up to 63 sub-flows per
     issue, observably noisy.
   - Why not `watch()`: re-traversal is only needed at pass
     boundaries; live tailing within a pass adds nothing.
3. **Per-role-step formula contract.** New formula registered as
   `po.formulas` entry-point `per-role-step` (pack-side, ~80 LOC),
   signature: `per_role_step(issue_id: str, rig: str, rig_path: str,
   role: str | None = None, parent_bead: str | None = None,
   dry_run: bool = False) -> dict`. The graph-traversal layer passes
   `issue_id` = the role-step bead id (e.g. `<seed>.role.builder.iter1`);
   `role` is sniffed from the bead title (`role:<name>` prefix) or
   the bead id pattern when unset. Internally it: looks up the seed
   bead via `resolve_seed_bead`, builds a `RoleRegistry` keyed on the
   seed (so session UUIDs live in **one** place across all role-step
   tasks), looks up the matching `@task` from `software_dev.py` via
   a small `ROLE_TASKS` dict, calls it with the assembled `ctx`, and
   returns its return value. The agent inside the task closes its
   own bead.
4. **Seed bead structure.** **Input issue itself is the seed; the
   formula creates N child role-step beads under it.** Naming:
   `<issue>.role.<role>.iter<N>` for any role that supports
   iteration (build, plan, verify, ralph), `<issue>.role.<role>` for
   one-shot roles (triage, baseline, deploy_smoke, …). Edges: `blocks`
   between sequential roles (`triage` → `baseline` → `plan.iter1` →
   `build.iter1` → `lint.1` ∥ `unit.1` … → `regression-gate.iter1` →
   `review.iter1` → …). Same dot-suffix convention as today's
   `<issue>.lint.1`, `<issue>.plan.iter1`, `<issue>.build.iter1` —
   already supported by `_dot_suffix_children` and `list_subgraph`.
5. **Cap exhaustion.** Seed flow keeps a Python counter per-loop-role
   (`plan_iter_count`, `build_iter_count`, …). On each `graph_run`
   pass, after `graph_run` returns, the seed flow scans for newly
   opened iter beads created by critics. If the count for a role
   exceeds `iter_cap`, the seed flow itself appends a
   `bd close <iter-bead> --reason "cap-exhausted: …"` (idempotent
   per the 2026-04-28 probe in `software_dev.py:1006`) and does
   **not** include that bead in the next pass's submission set.
   Loop terminates the same way it does today.
6. **Inter-step context handoff.** The `ctx` dict is reconstructed
   inside `per_role_step` from: `_load_rig_env(rig_path)`,
   `build_registry(seed_id, …)` (yields `base_ctx` with `run_dir`,
   `pack_path`, `flow_run_id`, …), plus per-iter values
   (`iter`, `plan_iter`, `verify_iter`, `ralph_iter`,
   `build_iter_description`, …) read from the role-step bead's
   description / metadata. Cross-role state that today flows
   through Python (`has_ui`, `is_docs_only`, `force_full_regression`)
   keeps its current home: the seed bead's `bd update --metadata`
   (already used: `store.set("has_ui", …)`, `store.set("plan_iter_final", …)`).
7. **Role-session affinity across separate flow runs.**
   `RoleSessionStore` is **already** seed-bead-keyed (see
   `role_sessions.py:53,82-101`). Two parallel `per_role_step`
   tasks for different roles read/write disjoint keys; two
   sequential tasks for the **same** role on different iter beads
   read the same key and `--resume <uuid>` correctly. No new
   plumbing required. Possible races (parallel lint + unit tasks
   both mutating tester role) are already handled today by the
   per-role lock that `RoleSessionStore` doesn't have, but neither
   does the legacy flow — we ship parity, not fixes.
8. **Dynamic graph extension.** Path (a) above: critic-creates +
   seed-flow-rediscovers between passes. The seed flow's
   `graph_run` invocation passes `formula="per-role-step"` so the
   formula contract holds. Each pass's submission set is computed
   as `list_subgraph(seed) - already_dispatched - cap_exhausted`.
9. **`PO_FORMULA_MODE` plumbing.** Read at flow entry via
   `os.environ.get("PO_FORMULA_MODE", "legacy")`. The flow body is:
   ```python
   if os.environ.get("PO_FORMULA_MODE", "legacy") == "graph":
       return _graph_software_dev_full(...)
   return _legacy_software_dev_full(...)
   ```
   The current 305-line body is renamed `_legacy_software_dev_full`
   verbatim (no edits, no extraction); `_graph_software_dev_full`
   is the new <100 LOC seeder + watcher loop. Acceptance (c) is
   measured against the latter.

### Files to Modify

| File | Action | Description |
|---|---|---|
| `software-dev/po-formulas/po_formulas/software_dev.py` | Modify | Rename current `software_dev_full` body to `_legacy_software_dev_full`. Add new `software_dev_full` dispatcher (~10 LOC) and `_graph_software_dev_full` (~80 LOC). Add module-level `ROLE_TASKS` dict mapping role names → `@task` callables already defined here. |
| `software-dev/po-formulas/po_formulas/per_role_step.py` | Create | New module; ~120 LOC. Defines `per_role_step` `@flow`, the `_resolve_role_from_bead_id` helper, and the bead→`@task` dispatcher. Imports `ROLE_TASKS` from `software_dev.py`. |
| `software-dev/po-formulas/po_formulas/seed_graph.py` | Create | ~80 LOC. Builds the initial seed sub-graph: `seed_initial_graph(seed_id, rig_path, plan_iter_cap, …) → list[child_id]`. One pure function — no Prefect, no tasks — for unit testing. |
| `software-dev/po-formulas/pyproject.toml` | Modify | Add `per-role-step = "po_formulas.per_role_step:per_role_step"` under `[project.entry-points."po.formulas"]`. (Not exposed via `po list` filtering — fine; users invoke the seed formula, not this one directly. Could be hidden in a follow-up via an `internal=True` EP attribute, deferred.) |
| `software-dev/po-formulas/po_formulas/agents/build-critic/prompt.md` | Modify | Add a "REJECTED PATH" section instructing the critic, on rejection, to ALSO `bd create <parent>.build.iter<N+1> --blocks <this-bead>` with the described template. Same for `plan-critic/prompt.md` and (when added) `verifier/prompt.md`. Roughly +20 lines per prompt. |
| `prefect-orchestration/CLAUDE.md` | Modify | Add a `## PO_FORMULA_MODE` section under "Common workflows" describing legacy vs graph modes and how to opt in. |
| `prefect-orchestration/engdocs/formula-modes.md` | Create | New engdoc explaining the seed-bead + graph-mode model and pointing at the decision log. ~120 lines. |
| `software-dev/po-formulas/tests/test_seed_graph.py` | Create | Pure-function tests for `seed_initial_graph`. |
| `software-dev/po-formulas/tests/test_per_role_step.py` | Create | Mock-bead-row + ROLE_TASKS dispatch tests. |
| `software-dev/po-formulas/tests/test_software_dev_graph_mode.py` | Create | StubBackend + temp-rig integration test driving 1 plan-iter + 1 build-iter through graph mode and asserting closure. |

No core (`prefect_orchestration/`) changes required.

### Skeleton Code

#### `software_dev.py` dispatcher

```python
ROLE_TASKS: dict[str, Callable[[RoleRegistry, dict[str, Any]], Any]] = {
    "triager": triage,
    "tester-baseline": baseline,
    "builder-plan": plan,
    "plan-critic": critique_plan,
    "builder": build,
    "linter": lint,
    "tester-unit": lambda reg, ctx: run_tests(reg, ctx, layer="unit"),
    "tester-e2e": lambda reg, ctx: run_tests(reg, ctx, layer="e2e"),
    "tester-playwright": lambda reg, ctx: run_tests(reg, ctx, layer="playwright"),
    "tester-diff": compute_diff_tests,
    "tester-regression": regression_gate,
    "build-critic": review,
    "releaser-deploy-smoke": deploy_smoke,
    "releaser-review-artifacts": review_artifacts,
    "verifier": verification,
    "cleaner": ralph,
    "tester-full-gate": full_test_gate,
    "documenter": docs,
    "releaser-demo-video": demo_video,
    "releaser-learn": learn,
}


@flow(name="software_dev_full", flow_run_name="{issue_id}", log_prints=True)
def software_dev_full(issue_id: str, rig: str, rig_path: str, **kwargs: Any) -> dict[str, Any]:
    """Dispatcher. PO_FORMULA_MODE=graph → reactive bead-graph; else legacy."""
    if os.environ.get("PO_FORMULA_MODE", "legacy") == "graph":
        return _graph_software_dev_full(issue_id, rig, rig_path, **kwargs)
    return _legacy_software_dev_full(issue_id, rig, rig_path, **kwargs)
```

#### `_graph_software_dev_full` (target <100 LOC)

```python
def _graph_software_dev_full(
    issue_id: str, rig: str, rig_path: str,
    iter_cap: int = 3, plan_iter_cap: int = 2,
    verify_iter_cap: int = 3, ralph_iter_cap: int = 3,
    gate_iter_cap: int = 2, parent_bead: str | None = None,
    dry_run: bool = False, claim: bool = True,
    pack_path: str | None = None,
    force_full_regression: bool = False,
) -> dict[str, Any]:
    logger = get_run_logger()
    rig_path_p = Path(rig_path).expanduser().resolve()
    _load_rig_env(rig_path_p)
    if claim and not dry_run:
        claim_issue(issue_id, rig_path=rig_path_p)

    caps = {"iter_cap": iter_cap, "plan_iter_cap": plan_iter_cap,
            "verify_iter_cap": verify_iter_cap, "ralph_iter_cap": ralph_iter_cap,
            "gate_iter_cap": gate_iter_cap}
    seed_initial_graph(seed_id=issue_id, rig_path=rig_path_p,
                       caps=caps, force_full_regression=force_full_regression,
                       pack_path=pack_path)

    # Reactive passes: graph_run dispatches the OPEN frontier (closed
    # beads filtered by `include_closed=False` inside list_subgraph);
    # critics may extend the graph with iter+1 beads; rerun until a
    # pass submits zero nodes (steady state) or `_MAX_PASSES` hit.
    cap_counts: dict[str, int] = {"plan": 0, "build": 0, "verify": 0,
                                  "ralph": 0, "gate": 0}
    for pass_n in range(1, _MAX_PASSES + 1):
        # Cap enforcement BEFORE dispatch: close any iter bead that
        # would exceed its cap with `--reason "cap-exhausted: …"` so
        # graph_run's frontier excludes it next call.
        _enforce_caps(seed_id=issue_id, cap_counts=cap_counts, caps=caps,
                      rig_path=rig_path_p, logger=logger)
        result = graph_run(root_id=issue_id, rig=rig, rig_path=str(rig_path_p),
                            formula="per-role-step")
        if result.get("submitted", 0) == 0:
            break
    else:
        logger.warning(f"_MAX_PASSES={_MAX_PASSES} hit; closing")

    if claim and not dry_run:
        close_issue(issue_id, notes="po graph-mode complete")
    return {"status": "completed", "passes": pass_n, "dispatched": len(dispatched)}
```

(Above sketch is ~55 lines; full version with imports/comments still
under 100. `_MAX_PASSES = 12` is a safety stop; sum of all caps in
default config is `2+3+3+3+2 = 13`, so 12 is a reasonable hard guard
beyond which something is definitely wrong.)

`graph_run` already accepts `child_ids` (per `graph_run` docstring
line 24); we pass the explicit frontier so each pass dispatches only
the newly-open beads.

#### `seed_initial_graph(seed_id, rig_path, caps, …)`

Pure function. Drops these beads under `seed_id` with these `--blocks`
edges (mirrors today's first pass exactly):

```
<seed>.role.triager
<seed>.role.tester-baseline       blocks: triager
<seed>.role.builder-plan.iter1    blocks: tester-baseline
<seed>.role.plan-critic.iter1     blocks: builder-plan.iter1
<seed>.role.builder.iter1         blocks: plan-critic.iter1
<seed>.role.linter.iter1          blocks: builder.iter1
<seed>.role.tester-unit.iter1     blocks: builder.iter1
<seed>.role.tester-e2e.iter1      blocks: builder.iter1
<seed>.role.tester-diff.iter1     blocks: linter.iter1, tester-unit.iter1, tester-e2e.iter1
<seed>.role.tester-regression.iter1   blocks: tester-diff.iter1
<seed>.role.build-critic.iter1    blocks: tester-regression.iter1
<seed>.role.releaser-deploy-smoke blocks: build-critic.iter1
<seed>.role.releaser-review-artifacts  blocks: releaser-deploy-smoke
<seed>.role.verifier.iter1        blocks: releaser-review-artifacts
<seed>.role.cleaner.iter1         blocks: verifier.iter1
<seed>.role.tester-full-gate.iter1 blocks: cleaner.iter1
<seed>.role.documenter            blocks: tester-full-gate.iter1
<seed>.role.releaser-demo-video   blocks: documenter (skipped if !has_ui)
<seed>.role.releaser-learn        blocks: releaser-demo-video || documenter
```

Critic-driven extensions on rejection:

- `plan-critic.iterN` close-rejected → critic creates
  `builder-plan.iter(N+1)`, `plan-critic.iter(N+1)`,
  rewires `builder.iter1.blocks` to `plan-critic.iter(N+1)`.
- `build-critic.iterN` close-rejected → critic creates
  `builder.iter(N+1)` + the same fan-out to lint/unit/e2e/diff/
  regression/build-critic at `iter(N+1)`, plus rewires the next
  blocker.
- `tester-regression.iterN` close-`regression_detected` → critic
  is the same role; same pattern.
- `verifier.iterN` close-rejected → critic creates the entire
  build-loop again at `iter(N+1)` (matches legacy).
- `cleaner.iterN` close-`ralph_found_improvement` → next iter.
- `tester-full-gate.iterN` close-failed → cleaner.iter(N+1) +
  tester-full-gate.iter(N+1).

Rewiring `--blocks` on existing open beads: `bd dep add <a> --type
blocks --to <b>`. (Read `beads_meta._bd_dep_list` source to confirm
syntax — already used by `list_subgraph`.)

**Multi-blocker beads.** `create_child_bead` (today) accepts a single
`blocks` id (`beads_meta.py:157,199`). For nodes with multiple
blockers (e.g. `tester-diff.iter1` blocks-by all of
[linter, unit, e2e]), `seed_initial_graph` calls
`create_child_bead(..., blocks=<first>)` then shells
`bd dep add <child> --type blocks --to <other>` for each remaining
blocker. We do NOT extend `create_child_bead` in this issue
(keeps blast radius small; the helper change is a separate
ergonomic improvement worth its own bead — file as 7vs follow-up).

#### `per_role_step.py`

```python
@flow(name="per_role_step", flow_run_name="{issue_id}")
def per_role_step(issue_id: str, rig: str, rig_path: str,
                  role: str | None = None, parent_bead: str | None = None,
                  dry_run: bool = False) -> dict[str, Any]:
    """Run ONE role-step `@task` from software_dev.py against `issue_id`.

    `issue_id` here is a role-step bead id like `<seed>.role.builder.iter1`.
    `role` is parsed from the bead id when None.
    """
    seed_id = parent_bead or resolve_seed_bead(issue_id, rig_path=rig_path)
    role_key = role or _parse_role_from_bead_id(issue_id, seed_id)
    fn = ROLE_TASKS.get(role_key)
    if fn is None:
        raise ValueError(f"unknown role-step {role_key!r} for bead {issue_id}")
    reg, base_ctx = build_registry(issue_id=seed_id, rig=rig, rig_path=rig_path,
                                    agents_dir=_AGENTS_DIR, dry_run=dry_run,
                                    claim=False, roles=SOFTWARE_DEV_ROLES)
    ctx = {**base_ctx, **_per_iter_context(issue_id, seed_id, role_key,
                                           Path(base_ctx["run_dir"]))}
    return fn(reg, ctx)
```

`_per_iter_context` parses `iter1` → `{"iter": 1}` etc. and rebuilds
the iter-description fields from the bead row's `description` (which
the seed flow / critic wrote there).

### Implementation Steps

1. **Land `seed_initial_graph` + tests.** Pure function; no Prefect.
   Verify by running unit tests against a temp `bd init` rig (already
   done in pack tests today via the pack's e2e harness — check what
   exists; mock `create_child_bead` if needed).
2. **Land `per_role_step` + ROLE_TASKS export.** EP registration; run
   `po packs update` locally; `po show per-role-step` should print
   the signature.
3. **Land `_graph_software_dev_full` dispatcher.** With
   `_MAX_PASSES=12` safety. LOC count check: `awk` from
   `def _graph_software_dev_full` to its closing `return`.
   **Checkpoint:** legacy mode still passes the full pack unit
   suite (`uv run python -m pytest`).
4. **Update three critic prompts** (plan-critic, build-critic,
   verifier) to instruct iter+1 bead creation on the rejected path.
   Use literal `bd create … --blocks …` examples; per
   `principles.md§"Prompt authoring convention"` no Jinja, just
   markdown.
5. **Land integration test `test_software_dev_graph_mode.py`** using
   `StubBackend` (`PO_BACKEND=stub`). Stub critic responses scripted
   to: pass 1 = plan-rejected, pass 2 = plan-approved, pass 3 =
   build-rejected, pass 4 = build-approved, … verify-approved,
   ralph-no-improvement, gate-pass. Assert that after the seed flow
   returns, the seed bead is closed and the run-dir contains
   verdicts/triage.json + the iter beads exist with closed status.
6. **Doc updates.** `engdocs/formula-modes.md` + `CLAUDE.md`
   pointer.

### Testing Strategy

| Layer | Test | Asserts |
|---|---|---|
| unit | `test_seed_graph.py::test_initial_graph_is_topo_sortable` | `seed_initial_graph()` writes a graph whose `topo_sort_blocks` produces a deterministic order matching the legacy flow's task sequence. |
| unit | `test_seed_graph.py::test_skips_demo_video_without_ui` | Conditional skip semantics preserved. |
| unit | `test_per_role_step.py::test_role_parsed_from_bead_id` | `<seed>.role.builder.iter3` → `role="builder"`, `iter=3`. |
| unit | `test_per_role_step.py::test_role_task_dispatch` | ROLE_TASKS lookup; mock the actual task and assert it's called with the right ctx. |
| unit | `test_software_dev_graph_mode.py::test_graph_mode_dispatcher_picks_branch` | `PO_FORMULA_MODE=graph` calls `_graph_software_dev_full`; default and `legacy` call legacy. |
| unit | `test_software_dev_graph_mode.py::test_graph_loc_under_100` | Reads `software_dev.py` source, slices the `_graph_software_dev_full` body, asserts LOC < 100. (Acceptance criterion (c) wired into CI.) |
| e2e (StubBackend) | `test_software_dev_graph_mode.py::test_one_plan_iter_one_build_iter` | Drives the pipeline through 1 plan-reject → approve → 1 build-reject → approve → verify-approve → no-ralph → gate-pass. Asserts seed bead is closed, all iter beads are closed, no orphans. |
| manual | Real Claude run on `prefect-orchestration-7vs.5.demo` (a synthetic tiny issue) | Acceptance (a): `PO_FORMULA_MODE=graph po run software-dev-full --issue-id … --rig prefect-orchestration --rig-path .` completes and closes. |

Stub-backend driven tests catch all regressions of the formula
control-flow without spending on Claude. The single manual real-Claude
run validates AC (a). We'll run it on a "rename a constant" tier
issue to keep cost ~$1.

### Verification Strategy

| AC | Check | Where |
|---|---|---|
| (a) `PO_FORMULA_MODE=graph` runs end-to-end | Manual run on a tiny throwaway issue (`software_dev_full --dry-run` first to validate the seed graph; then real run on `7vs.5.demo`). Verifier bead shows `bd show <issue>` status=closed. | Builder runs locally, verifier role checks `bd show` + `.planning/.../verdicts/`. |
| (b) `PO_FORMULA_MODE=legacy` still works | Pack unit suite (`uv run python -m pytest`); the existing `test_software_dev_*` tests already exercise legacy mode and must stay green. Plus core unit suite (no regression beyond the 10 pre-existing failures in baseline.txt). | Verifier role runs `uv run python -m pytest` in both repos and diffs against baseline. |
| (c) Body <100 LOC | `test_graph_loc_under_100` (CI). Plus manual `awk 'NR>=START && NR<=END' software_dev.py | wc -l`. | Critic role + unit test. |
| (d) Documented | `engdocs/formula-modes.md` exists; `CLAUDE.md` mentions `PO_FORMULA_MODE`. Grep both files. | Verifier role runs `grep -l PO_FORMULA_MODE engdocs/ CLAUDE.md`. |

### Migration Plan

1. Ship code in 1 PR with `PO_FORMULA_MODE=legacy` as default. No
   user-visible behavior change.
2. Dogfood graph mode on `7vs.5.demo` (a hand-crafted trivial issue
   under this rig). If clean, dogfood on next routine `prefect-orchestration-*`
   issue with `PO_FORMULA_MODE=graph` set in `.po-env`.
3. After 3+ green runs across this rig + one external rig, flip the
   default to `graph` (separate issue, NOT 7vs.5).
4. After 1 month at default-graph, delete legacy in 7vs.6.

### Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Critic agents fail to create iter+1 beads on rejection (prompt regression) | Medium | High (loop deadlocks) | The seed flow's `_MAX_PASSES` cap closes the run with a logged warning rather than hanging; integration test scripts the rejected path explicitly. |
| Dolt-server lock contention with parallel role-step tasks | Low | Medium (intermittent `bd` failures) | This rig is already on dolt-server (per CLAUDE.md "Backend (dolt-server)"); existing `lint ∥ unit ∥ e2e` parallelism in legacy mode already exercises 3-way concurrency. Graph mode's per-pass concurrency is the same. |
| Session-UUID drift across separate Prefect tasks | Low | Medium (Claude session forks) | `RoleSessionStore` is already seed-bead-keyed and writes via `bd update --metadata` (atomic). Verified by reading `role_sessions.py:101`. |
| `bd dep add` rewiring race (critic adds new edge while seed flow is `list_subgraph`-ing) | Low | Low (one extra pass to converge) | Pass-boundary snapshot model is robust to this — if missed in pass N, picked up in pass N+1. |
| LOC budget creep | Medium | Low (test fails) | LOC test in CI (`test_graph_loc_under_100`); pushes back at PR time. |
| Bead-id-to-role parsing brittleness (e.g. `<seed>.role.tester-e2e.iter1`) | Medium | Medium | Use `_parse_role_from_bead_id` with explicit fixture tests for every key in ROLE_TASKS. |
| `force_full_regression` / `pack_path` / docs-only branch parity | Medium | Medium | seed_initial_graph reads triage flags from the seed bead's metadata AFTER the triage role-step bead closes — so docs-only emission happens during pass 2, matching legacy short-circuit semantics. Test scripts both paths. |

### Deferred (out of scope for this issue)

- **7vs.6** — delete the `verdicts/` file convention + `prompt_for_verdict`
  + `_legacy_software_dev_full`. **NOT in this issue.**
- **7vs.7** — `<seed>.role.<role>.iter<N>` namespace hygiene (drop the
  `role.` prefix? hide internal `per-role-step` from `po list`?). NOT
  in this issue.
- **dolt change-feed event bus.** Today's `beads_meta.watch()` is
  poll-based. Upgrading to dolt's binlog would let `_graph_software_dev_full`
  use `watch()` instead of pass-boundary snapshots, with sub-second
  reactive latency. NOT in this issue (the docstring at
  `beads_meta.py:888-893` already calls out this upgrade path).
- **Hide `per-role-step` from `po list`.** Add an `internal=True` EP
  attribute or filter by name prefix. NOT in this issue.
- **Replace `_MAX_PASSES` heuristic with a precise per-role cap-sum
  ceiling.** NOT in this issue — `_MAX_PASSES = 12` is a safety belt,
  not the actual stop condition (cap-exhaustion is).
- **Migrate `epic_run` / `graph_run` callers to graph mode by default.**
  NOT in this issue.

## Questions and Clarifications

1. **Should `per-role-step` be filterable out of `po list`?** Current
   approach: register normally, document in `engdocs/formula-modes.md`
   that "users invoke `software-dev-full`; the dispatcher uses
   `per-role-step` internally." Alternative: ship a hidden-EP convention
   in core (separate issue). Recommendation: defer to 7vs.7.

2. **Should the critic agent's iter+1 bead description be machine-
   generated by the orchestrator vs prompted into the agent?** Memory
   note says critic. Today's `_build_critic_iter_description` is a
   ~30-line Python template; replicating in a prompt verbatim is fine
   (duplication preferred over Jinja per principles). Recommendation:
   **prompt** path; orchestrator never owns iter+1 in graph mode.

3. **`graph_run` invocation: pass full subgraph each pass or just the
   frontier?** **Verified:** `child_ids` kwarg is on `epic_run`
   (`epic.py:82`), NOT `graph_run`. Two options:
   (a) **Plumb `child_ids` through `graph_run`** (~10 LOC change in
   `graph.py`: accept `child_ids: list[str] | None = None`; when
   non-None, bypass `list_subgraph` and dispatch exactly those ids).
   This keeps the seed flow simple.
   (b) Don't add `child_ids` to `graph_run`; let the seed flow rely
   on `include_closed=False` so already-closed iter beads are
   skipped automatically (which is the correct semantic anyway —
   `dispatched` set is a Python-side dedup that's redundant with
   bd's own state).
   **Recommendation: (b).** Smaller change, leans on existing
   `include_closed=False` filtering in `list_subgraph`, no core/pack
   API surface growth (principles §1). The seed flow's `dispatched`
   set becomes unnecessary; we just call `graph_run(root_id=issue_id,
   ...)` repeatedly until the per-pass `submitted` count is zero or
   `_MAX_PASSES` hit. Update the skeleton above accordingly.

## Review History

(Pending plan-reviewer pass.)
