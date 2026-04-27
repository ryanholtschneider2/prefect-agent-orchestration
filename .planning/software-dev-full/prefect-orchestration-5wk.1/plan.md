# Plan — prefect-orchestration-5wk.1

## Goal
Add a new lightweight `minimal-task` PO formula that runs only
`triage → plan → build → lint → close`, skipping critique iters,
regression-gate, deploy-smoke, review-artifacts, verification, ralph,
docs, and learn. Used for 100-way fanout demos (snake-bead epic) where
running the full actor-critic loop on every trivial child wastes
tokens. Fail-out (no ralph fallback) when lint fails twice.

## Pack-path note
Per CLAUDE.md / bead `pw4`, formula code lives in the **sibling pack**
`po-formulas-software-dev`, not in the core repo. The bead's
`po.target_pack` may not be set; builder MUST `cd` into
`/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas`
to add the formula and register the entry point, then run
`po packs update` from this rig so `importlib.metadata` picks up the
new entry-point.

## Affected files

### Pack repo (`/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/`)
- `po_formulas/minimal_task.py` — **new** module containing the
  `@flow def minimal_task(...)` flow plus its task wrappers.
- `po_formulas/__init__.py` — add `from po_formulas.minimal_task import minimal_task` so the symbol is importable.
- `pyproject.toml` — add entry point under `[project.entry-points."po.formulas"]`:
  `minimal-task = "po_formulas.minimal_task:minimal_task"`.
- `po_formulas/agents/` — reuse existing role prompt dirs (`triager`,
  `planner`, `builder`, `linter`). No new prompt files unless
  duplication is judged necessary during build (see Approach §Prompt reuse).
- `README.md` — add a "minimal-task" subsection under the formulas list
  describing pipeline shape, when to use, fail-out semantics.

### Core repo (`/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/`)
- `engdocs/` — add a short note (either appended to an existing doc such
  as `engdocs/principles.md` or a new `engdocs/minimal-task.md`)
  documenting that a fast lightweight formula exists for fanout demos
  and pointing readers at the pack docs.
- `CLAUDE.md` (project) — one-line addition under "Common workflows /
  Running a beads issue end-to-end" mentioning `po run minimal-task`
  for trivial fanout children.

No changes to `prefect_orchestration/` core source.

## Approach

### Pipeline shape
```
claim_issue (via build_registry) →
  triage →
  plan          (single pass, no plan-critic)
  build         (single iteration)
  lint          (1st pass; reads $RUN_DIR/verdicts/lint-iter-1.json)
  if lint fails:
      build    (2nd iter — builder reads lint failure for fix)
      lint     (2nd pass)
      if still fails: raise → flow fails, bead left in_progress
  close_issue (on success)
```

No baseline, no critique loop, no regression-gate, no review, no
verification, no ralph, no docs, no demo, no learn.

### Reuse existing tasks
The new module imports the @task definitions already in
`software_dev.py` rather than redefining them — `triage`, `plan`,
`build`, `lint` are pure `@task`-decorated functions taking
`(reg, ctx)` and writing the same verdict files. Keeping them shared:
- preserves `verdicts/<step>.json` artifact compatibility for
  `po artifacts` / `po watch` / `po logs`.
- preserves the per-role tag concurrency limits.
- keeps a single source of truth for prompt rendering.

The `minimal_task` flow constructs its own `RoleRegistry` via
`build_registry(...)` with a trimmed `roles=[...]` set: just
`triager`, `builder`, `linter` (the planner and build use the
`builder` session; linter has its own; tester/critic/verifier roles
are omitted entirely so no Claude sessions are spawned for them).

### Lint-verdict reading
Existing `lint` task writes a markdown log (`lint-iter-N.log`) but
not a structured verdict. To gate the loop programmatically without
parsing prose, one of two options (decide during build, prefer (a)):

(a) **Add a verdict-write step to the linter prompt.** The linter
prompt (`agents/linter/prompt.md`) is asked to additionally write
`$RUN_DIR/verdicts/lint-iter-{iter}.json` with `{"verdict": "pass"|"fail", "summary": "..."}`. The `lint` task body in
`software_dev.py` already calls `sess.prompt(...)`; a thin wrapper in
`minimal_task.py` can call `read_verdict(run_dir, f"lint-iter-{iter}")`
afterward. If the linter prompt isn't already writing a verdict file,
this minor change is additive — `software_dev_full` ignores the
verdict so it's backwards-compatible.

(b) Run `ruff` / pytest in-process via `subprocess.run` from the
flow body and key off the exit code. Less consistent with PO's
"agents write verdicts" convention; only fall back to this if (a)
turns out to require larger prompt rewrites.

### Prompt reuse vs. trim
Default to **reuse-as-is** for `triager`, `planner`, `builder`,
`linter`. Existing prose may mention "critique iters" or "ralph" —
acceptable for v1 because the references are advisory; the flow
itself enforces the shape. If during build the cross-references
prove confusing for a single-pass run, fall back to **per-role
overrides** by adding `agents/minimal-<role>/prompt.md` files and
have `minimal_task.render(...)` look those up first with a fallback
to the shared role. Do NOT proliferate copies unless required.

### Bead lifecycle
Identical to `software_dev_full`: `build_registry(... claim=True)`
runs `claim_issue` on entry; on the success path the flow calls
`close_issue(issue_id, notes=f"po-{fr_id[:8]} minimal-task complete")`.
On failure (lint fails twice) the flow raises — bead stays
`in_progress`, run_dir artifacts remain for `po artifacts`.

### Flow signature (sketch)
```python
@flow(name="minimal_task", flow_run_name="{issue_id}", log_prints=True)
def minimal_task(
    issue_id: str,
    rig: str,
    rig_path: str,
    pack_path: str | None = None,
    parent_bead: str | None = None,
    dry_run: bool = False,
    claim: bool = True,
) -> dict[str, Any]:
    ...
```

The kwargs match `software_dev_full`'s subset so the snake-bead
epic dispatcher (`5wk.5`) can swap formulas without changing
arg-passing.

## Acceptance criteria
Verbatim from the bead (per triage summary):
1. New formula `minimal-task` registered via the `po.formulas` entry point.
2. Pipeline runs only `triage → plan → build → lint → close` (skips critique iters, regression-gate, deploy-smoke, review-artifacts, verification, ralph, docs, learn).
3. Hello-world bead completes in under 4 minutes (goal, not hard gate per triage).
4. Reuses existing role prompts where suitable.
5. Documented in pack README + engdocs.
6. Fails out (no ralph fallback) when lint fails twice.

## Verification strategy

| AC | How verified |
|---|---|
| 1 | `po list` includes `minimal-task` row (after `po packs update`). `po show minimal-task` prints signature + docstring. |
| 2 | Inspect new flow body in `minimal_task.py`: only `triage`, `plan`, `build`, `lint` task calls; no `regression_gate` / `review` / `verification` / `ralph` / `docs` / `demo_video` / `learn` / `deploy_smoke` / `review_artifacts` / `baseline` / `critique_plan`. e2e dry-run smoke (below) confirms verdict files written are limited to those four steps. |
| 3 | Manual / informal — measure wall clock on a hello-world bead during build. Not a CI gate. |
| 4 | Diff shows `minimal_task.py` calls the **same** `triage`/`plan`/`build`/`lint` task objects imported from `software_dev.py` (or, if duplicated, that the prompt files are reused via the same `agents/` dirs). |
| 5 | Pack `README.md` contains a `minimal-task` heading; engdocs note exists in core repo. |
| 6 | New e2e/unit test asserts that with a stub linter verdict of `{"verdict": "fail"}` returned twice in a row, the flow raises (or returns a `failed` status) without invoking a third build iteration and without calling `close_issue`. |

## Test plan

- **unit** (`tests/`) — add `tests/test_minimal_task.py` in the **pack
  repo** (`software-dev/po-formulas/tests/`) with `StubBackend`
  fixtures verifying:
  - the flow registers under entry-point name `minimal-task` (via
    `importlib.metadata.entry_points(group='po.formulas')`).
  - happy path: stub returns `{"verdict": "pass"}` for triage and
    `{"verdict": "pass"}` for lint → flow returns `status=="completed"`,
    `close_issue` called once.
  - failure path: stub returns `{"verdict": "fail"}` for lint twice in
    a row → flow raises `RuntimeError` (or returns `status=="failed"`),
    `close_issue` NOT called, exactly two build iterations executed.
- **e2e** (`tests/e2e/`) — add one subprocess test invoking
  `po run minimal-task --issue-id <fixture-bead> --rig … --rig-path …
  --dry-run` and asserting exit code 0 plus presence of
  `verdicts/triage.json`, `verdicts/lint-iter-1.json`, no
  `verdicts/regression-iter-*.json` / `verdicts/verify-iter-*.json` in
  the run dir.
- **playwright** — N/A (no UI).

Tests live in the **pack** because the formula lives in the pack;
core's `tests/` stays free of pack imports.

## Risks
- **Pre-existing baseline import error** (`prompt_for_verdict` not
  importable) shown in `baseline.txt` is stale — `parsing.py` does
  export `prompt_for_verdict` at line 32. Likely a stale install; run
  `po packs update` early to refresh entry-point + module metadata, and
  re-run baseline to confirm before declaring a regression.
- **Pack vs rig path confusion** — bead has
  `po.target_pack == po.rig_path`, but pack code belongs in the
  sibling repo. Builder must NOT add `minimal_task.py` under
  `prefect_orchestration/`; it must land in
  `software-dev/po-formulas/po_formulas/`. Two `git` repos receive
  commits.
- **Prompt drift** — reusing `builder`/`linter` prompts that mention
  the full pipeline could mislead the agent on a single-iteration
  run. Mitigation: leave reuse-as-is for v1, add minimal-prefixed
  variants only if observed behavior demands it.
- **Verdict-file convention** — adding a JSON-verdict requirement to
  the linter prompt is a behavioral change for the existing
  `software_dev_full` flow too. Confirm `software_dev_full` does not
  read `verdicts/lint-iter-*.json` (current code reads only the
  `.log`) before extending the linter prompt; otherwise the change is
  additive and safe.
- **Snake-bead consumer (5wk.5)** assumes formula name `minimal-task`
  literally. Do not rename mid-build.
- **No API contract changes**, **no migrations**, **no breaking
  consumers** of core. The change is additive: a new entry-point row,
  a new flow module, prompt reuse.
- **Concurrent epic dispatch** — `minimal-task` will run under the
  same Prefect work pool as `software-dev-full`. If the snake epic
  fans out 100 instances, raise `prefect work-pool` concurrency or
  provision a dedicated pool — out of scope for this bead but worth
  noting for `5wk.5`.
