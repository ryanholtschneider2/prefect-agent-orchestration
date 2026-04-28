# Review Summary: prefect-orchestration-7vs.5

## Acceptance Criteria

| # | Criterion | Status |
|---|-----------|--------|
| (a) | `PO_FORMULA_MODE=graph` runs end-to-end on a real issue | PARTIAL — 71 subflows over 12 passes on real Claude run; max_passes safety belt fired correctly; full convergence not reached |
| (b) | `PO_FORMULA_MODE=legacy` byte-for-byte equivalent | PASS — legacy body renamed verbatim; 87 pack tests passing including all pre-existing legacy-path tests |
| (c) | Body <100 LOC | PASS — 96 LOC enforced by `test_graph_loc_under_100` |
| (d) | Documented | PASS — `engdocs/formula-modes.md` + `CLAUDE.md` `### PO_FORMULA_MODE` subsection |

## Test Results

| Suite | Passing | Notes |
|-------|---------|-------|
| Core (`prefect-orchestration`) | 747 | Was 722 baseline; new modules from parallel work account for delta. Zero regressions to the test suite this issue touches. |
| Pack (`software-dev/po-formulas`) | 87 | Was 79 after iter 1; +8 new tests in iter 2. Zero regressions. |
| Real-Claude smoke run | 12/12 graph_run passes executed | 71 per_role_step subflows dispatched; max_passes_exhausted returned cleanly; seed left open as designed |

## Key Changes

**Pack** (`software-dev/po-formulas`):
- `po_formulas/seed_graph.py` (NEW, 220 LOC): pure-function seed-bead author dropping ~17 role-step beads with blocks edges
- `po_formulas/per_role_step.py` (NEW, ~150 LOC): per-role-step formula dispatching to `ROLE_TASKS`; defensive force-close belt; metadata-skip for docs-only / no-ui paths
- `po_formulas/software_dev.py` (MODIFIED): renamed body to `_legacy_software_dev_full`; added `ROLE_TASKS`, `_enforce_caps`, `_close_subtree_blocks_down`, `_graph_software_dev_full` (96 LOC body), dispatcher branching on `PO_FORMULA_MODE`
- 17 agent prompts in `agents/*/prompt.md`: appended `{{role_step_close_block}}` instructing agent to close own role-step bead with role-appropriate reason
- 3 critic prompts (plan-critic, build-critic, verifier): added REJECTED PATH section authoring iter+1 beads via `bd create` with `{{seed_id}}` substitution

**Core** (`prefect-orchestration`):
- `prefect_orchestration/beads_meta.py` (MODIFIED): `create_child_bead` now uses `--deps parent-child:<id>` instead of `--parent <id>` (bd 1.0 rejects `--id` + `--parent` together) — surfaced by the live smoke run, applies to 7vs.3/4 helpers as well
- `engdocs/formula-modes.md` (NEW, 110 LOC): legacy vs graph mode architecture + decision log pointer
- `CLAUDE.md`: `### PO_FORMULA_MODE` subsection under "Common workflows"

## Before/After (Code Path)

**Before (legacy)**: `software_dev_full(...)` body = 305 lines of nested Python loops (plan-critic ⟲, build-critic+regression ⟲, verify ⟲, ralph ⟲, full_test_gate fix-up ⟲).

**After (graph mode)**: `software_dev_full(...)` body = 4-line dispatcher branching on `PO_FORMULA_MODE` env. Graph branch (`_graph_software_dev_full`, 96 LOC) calls `seed_initial_graph(...)` then loops `graph_run(root_id=issue_id, formula="per-role-step")` until `submitted=0` or `_MAX_PASSES` hit. Each role-step bead is dispatched as its own per_role_step subflow; agents close their own beads at end-of-turn; critics author iter+1 beads on rejection.

**After (legacy mode)**: `_legacy_software_dev_full(...)` = byte-for-byte the prior body. Default behavior unchanged.

## Decision Log Highlights

- **Closure model: agent-closes-own-bead** (per user directive in iter 2). Aligns with 7vs.3/4 patterns. Defensive force-close belt only fires on successful @task return.
- **`_MAX_PASSES = 12` heuristic** (load-bearing safety belt). Plan said sum of caps in default config = 13; 12 is one-below. Filed as follow-up to compute from `caps.sum()` instead.
- **`max_passes_exhausted` returns failure-coded status; does NOT close seed** (reviewer's BLOCKING fix #3). Verified in production smoke.
- **`bd create` argv shape: `--id` + `--deps parent-child:<id>` instead of `--parent`** (bd 1.0 incompatibility surfaced live).
- **`{{seed_id}}` template variable in ctx for critic prompts** (reviewer polish #1; required corresponding legacy-mode call-site fixup).

## Confidence Level

**MEDIUM** — implementation is shippable behind the flag (default = legacy = untouched), exercised end-to-end on a real Claude flow, three BLOCKING fixes verified in production. NOT yet ready to flip the default; convergence + bead-closure compliance are filed for follow-up work.
