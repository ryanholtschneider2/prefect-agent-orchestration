# Implementation Summary: prefect-orchestration-7vs.4

## What changed

Migrated the `critique_plan` and `review` (build-critic) loops from
`verdicts/<step>.json` file artifacts to bead-mediated handoff,
mirroring the 7vs.3 lint pilot pattern.

## Files changed

### Core (`prefect-orchestration/`)

- `prefect_orchestration/beads_meta.py` (+51 lines)
  - `create_child_bead`: added optional `blocks: str | None = None`
    kwarg → emits `--deps blocks:<id>` when set. Idempotent path
    unchanged.
  - New `read_iter_cap(parent_id, default, *, rig_path,
    metadata_key="po.iter_cap")` helper: reads metadata key from
    parent bead, falls back to default on missing/non-int/non-positive.
- `tests/test_beads_meta.py` (+151 lines): 8 new tests covering
  `--deps blocks:` forwarding (3) and `read_iter_cap` decoding (5).

### Pack (`software-dev/po-formulas/`)

- `po_formulas/software_dev.py` (+259/-46):
  - New helpers `_read_critic_verdict` and
    `_build_critic_iter_description`.
  - `critique_plan` task: rewritten to create
    `<parent>.plan.iter<N>` bead, render prompt with bead id, decode
    final state via `_read_critic_verdict`.
  - `review` task: rewritten symmetrically for
    `<parent>.build.iter<N>`.
  - `software_dev_full` flow body: plan + build loops now call
    `read_iter_cap`, build self-sufficient iter descriptions,
    track `prev_plan_iter_bead` / `prev_build_iter_bead` for the
    `--deps blocks:` chain, and orchestrator-close the cap-exhausted
    bead with `--reason "cap-exhausted: …"`.
- `po_formulas/agents/plan-critic/prompt.md` (+38/-20): rewritten
  for bead-close contract. Still writes
  `{{run_dir}}/plan-critique-iter-{{plan_iter}}.md` as artifact (so
  `po artifacts` keeps working) but the verdict signal is the
  `bd close` reason keyword (`approved` / `rejected`). No more
  `verdicts/plan-iter-N.json`.
- `po_formulas/agents/build-critic/prompt.md` (+39/-22): same shape
  for build review.
- `tests/test_software_dev_critic_bead.py` (+250 lines): 6 new
  tests — approved-on-iter1, rejected-then-approved with
  `--deps blocks:` chain assertion, self-sufficient description,
  3-iter cap-exhausted, agent-crash (bead left open), iter-cap
  metadata override.

## Acceptance criteria status

- (a) prompts updated → ✓ (both rewritten; verdict-file path retired
  with explicit "do NOT write" instruction).
- (b) 3-iter dep chain with `--deps blocks:` → ✓ (covered by
  `test_review_three_iter_cap_exhausted_chain`; assertion on the
  recorded `bd create` argv).
- (c) self-sufficient bead descriptions → ✓ (covered by
  `test_critique_plan_iter_description_self_contained`; description
  contains parent summary block + prior critique markdown + scope).
- (d) `iter_cap` honored, `--reason=cap-exhausted` close → ✓
  (orchestrator emits `bd close <iter<cap>> --reason "cap-exhausted:
  iter_cap=<N>"` after the critic agent rejects iter<cap>; flow
  proceeds to next step with `verdict="cap_exhausted"`). Backwards-
  compatible: `iter_cap` and `plan_iter_cap` kwargs preserved as
  fallback defaults.

## Test counts

- Core: **711 passed**, 10 failed (all pre-existing, unchanged from
  baseline 703+10+2). +8 new tests in `tests/test_beads_meta.py`.
- Pack: **51 passed**, 4 failed (all pre-existing, unchanged from
  baseline 45+4). +6 new tests in
  `tests/test_software_dev_critic_bead.py`.

## Out of scope (per plan)

- Verifier, full_test_gate, ralph, regression_gate, triager,
  tester — still file-based.
- Linter — already migrated by 7vs.3.
- `po artifacts` adapter for iter beads — future ergonomic work.
- Removing `iter_cap` / `plan_iter_cap` kwargs — kept as fallback.
