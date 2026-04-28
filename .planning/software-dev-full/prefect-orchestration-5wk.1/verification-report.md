# Verification Report: prefect-orchestration-5wk.1

## Acceptance Criteria

| # | Criterion | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | `po list` shows `minimal-task` | CLI smoke | PASS | `po list \| grep minimal-task` → `formula  minimal-task  po_formulas.minimal_task:minimal_task  Lightweight \`triage → plan → build → lint → close\` pipeline.` |
| 2 | Pipeline runs only triage→plan→build→lint→close | Code inspection | PASS | `po_formulas/minimal_task.py` flow body imports only `triage`, `plan`, `build`, `lint` from `software_dev`; no `regression_gate`/`review`/`verification`/`ralph`/`docs`/`demo_video`/`learn`/`deploy_smoke`/`review_artifacts`/`baseline`/`critique_plan` references. `po show minimal-task` confirms signature. |
| 3 | <4 min on hello-world bead | Manual (deferred) | DEFERRED | Per triage: goal not hard gate. Will be measured during snake-bead 5wk.5 demo run. |
| 4 | Reuses existing role prompts | Code inspection | PASS | Imports `triage`, `plan`, `build`, `lint` @task callables directly from `po_formulas.software_dev`; no new prompt directories created; existing `agents/{triager,planner,builder,linter}/prompt.md` reused. Linter prompt extended additively with verdict-file instructions (backwards compatible — `software_dev_full` ignores it). |
| 5 | Documented in pack README + engdocs | File check | PASS | Pack `README.md` has new "minimal-task" subsection; core `engdocs/minimal-task.md` created; `CLAUDE.md` one-line reference added. |
| 6 | Fails out (no ralph) when lint fails twice | Code inspection | PASS | Flow body: `for iter_num in (1, 2): build(); lint(); if read_verdict(...).verdict == "pass": break` else `raise RuntimeError(...)`. `close_issue` only called on the success path. |

## Regression Check
- Pack tests (sibling repo): 45 passed (2 pre-existing collection errors unrelated to this change — `_CODE_ROLES` import already broken at baseline).
- Core unit tests: 582 passed, 16 failed — all 16 failures are pre-existing (baseline had 27 incl. e2e). Failures are in `test_scheduling.py` / `test_status.py` / `test_agent_session_mail.py` etc., none of which exercise `po_formulas.minimal_task`. My core changes were docs-only (`engdocs/minimal-task.md` + `CLAUDE.md` line) and cannot affect test outcomes.
- Regressions introduced by this change: NONE.

## Live Environment Verification
- Environment: in-process import + CLI subprocess
- Smoke checks:
  - `uv run python -c "from po_formulas.minimal_task import minimal_task"` → OK
  - `uv run po list | grep minimal-task` → row present
  - `uv run po show minimal-task` → signature + docstring rendered correctly
- Live flow execution against a real bead: deferred to 5wk.5 (snake-bead seeder consumes this formula).

## Decision Log Review
See `decision-log.md`. Key decisions: (a) reused `software_dev` @task callables vs duplicating; (b) extended linter prompt additively rather than creating `minimal-linter` variant; (c) used `read_verdict()` over subprocess exit-code parsing.

## Confidence Level
**HIGH** for AC 1, 2, 4, 5, 6 (verified via direct CLI/import smoke + code inspection).
**DEFERRED** for AC 3 (wall-clock goal, measured downstream in 5wk.5 per triage).
