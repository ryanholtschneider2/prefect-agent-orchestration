# Implementation Summary: prefect-orchestration-7vs.3

## Issue

Pilot bead-mediated handoff for the lint role. The orchestrator
creates a child lint bead per iteration; the agent closes it with
`bd close --reason ...` (and `bd update --append-notes ...` on
failure). The flow reads the bead's final state instead of parsing
a `verdicts/lint-iter-N.json` file.

## What Was Implemented

### Files Modified

| File | Changes | LOC |
|------|---------|-----|
| `prefect-orchestration/prefect_orchestration/beads_meta.py` | Added `create_child_bead()` helper (idempotent on "already exists"). | +60 |
| `software-dev/po-formulas/po_formulas/software_dev.py` | Replaced `lint` task body with bead-mediated flow; added `_read_lint_verdict()` private helper; updated import to pull `_bd_show, create_child_bead`. | +~85 / -~10 |
| `software-dev/po-formulas/po_formulas/agents/linter/prompt.md` | Replaced "Verdict file" section with `bd close {{lint_bead_id}}` contract. | -~14 +~14 |
| `software-dev/po-formulas/po_formulas/minimal_task.py` | Dropped `read_verdict` import + use; consume `lint(...)` return directly; dropped now-unused `run_dir` local. | -~7 +~3 |

### Files Created

| File | Purpose | LOC |
|------|---------|-----|
| `software-dev/po-formulas/tests/test_software_dev_lint_bead.py` | Unit tests: clean-pass, fail-then-fix, agent-crash, idempotent create. | 255 |

### Key Implementation Details

- `create_child_bead` shells `bd create --id=<child_id> --parent=<parent_id> --title=... --description=... --type=task -p <prio>` with `cwd=rig_path`. On non-zero exit *and* `"already exists"` substring in stderr, returns the id (idempotent for retries). Other failures raise `RuntimeError` with bd's stderr embedded. `NotImplementedError` when `bd` is missing.
- `lint` task creates `<parent>.lint.<iter>` before prompting the agent, renders the prompt with `lint_bead_id={child_id}` so the prompt's `bd close` instructions name the right bead, and after the agent's turn calls `_read_lint_verdict(child_id, rig_path, iter)` to build the verdict dict.
- `_read_lint_verdict` reads `bd show --json` via the existing `_bd_show` helper. Logic:
  - `status == "closed"` AND `"clean"` substring (case-insensitive) in `closure_reason`/`reason` → `pass` (matches the prompt's "lint clean" close reason).
  - Closed with any other reason → `fail`, summary = first non-empty line of `notes` (or fall back to `reason`, or `"lint failed"`).
  - Still open → `fail`, summary = `"agent crash: lint bead left open"` (treat-as-fail-no-raise per plan §Decision points).
- `minimal_task` now consumes the verdict dict directly. `read_verdict` import removed; `run_dir` local removed (no longer used).

## Acceptance Criteria Status

| Criterion | Status | Notes |
|-----------|--------|-------|
| (a) Linter prompt updated with `bd close` contract | DONE | grep `'bd close.*lint_bead_id'` ≥ 1; grep `'verdicts/lint-iter'` == 0. |
| (b) Verdict file path removed for lint | DONE | `software_dev.py::lint` no longer renders/writes `verdicts/lint-iter-*.json`. |
| (c) End-to-end lifecycle | DONE (unit) | New tests assert `bd create` → agent closes → orchestrator reads. Manual `po run minimal-task` smoke not exercised in this implementation. |
| (d) Test coverage | DONE | 4 passing cases (3 from plan + idempotency for `create_child_bead`). |

## How to Demo

```bash
# Pack tests for the new contract:
cd /home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas
uv run python -m pytest tests/test_software_dev_lint_bead.py -v

# Full pack regression:
uv run python -m pytest --tb=short -q \
  --ignore=tests/test_software_dev_pack_path.py \
  --ignore=tests/test_software_dev_pack_path_metadata.py

# Core regression:
cd /home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration
uv run python -m pytest --tb=short -q --ignore=tests/e2e
```

For a real end-to-end smoke (out of scope for this builder phase):
`po run minimal-task --issue-id <some-trivial-bead> --rig <rig> --rig-path <path>` — `bd ls` should show `<id>.lint.1` open during the lint turn and closed afterwards.

## Test Results

### Pack (`software-dev/po-formulas`)

| | Before | After |
|---|---|---|
| Passed | 41 | 45 (+4 new) |
| Failed | 4 (`test_epic_discover_flags.py::*`, pre-existing) | 4 (same pre-existing) |
| Collection errors | 2 (`test_software_dev_pack_path*.py`, pre-existing — refer to `_CODE_ROLES`/`_resolve_pack_path` symbols that no longer exist) | 2 (same pre-existing) |

### Core (`prefect-orchestration`, `--ignore=tests/e2e`)

| | Before | After |
|---|---|---|
| Passed | 703 | 703 |
| Failed | 10 (cli_packs / deployments / mail) | 10 (same pre-existing) |
| Skipped | 2 | 2 |

No new failures. Pre-existing failures unrelated to this change.

## Deviations from Plan

- Added a 4th test (`test_create_child_bead_idempotent_on_already_exists`) beyond the 3 specified in the plan. It exercises the idempotency branch directly — cheap insurance since `_read_lint_verdict` paths cover the rest.
- `_read_lint_verdict` reads `closure_reason` first then falls back to `reason` (plan said "or"); summary falls back to `notes` first then `reason` then literal `"lint failed"`. Multi-line notes are reduced to the first non-empty line so the summary stays one-liner like the legacy contract.

## Known Issues or Limitations

- `_bd_show` returns are sensitive to whatever JSON `bd show --json` emits for `closure_reason` / `notes`. The verdict logic accepts both `closure_reason` and `reason` to be tolerant; if a different bd version uses yet another field name we'd need to extend that.
- Agent-crash detection (`status != "closed"`) doesn't time-bound: if the agent crashes immediately, `lint(...)` returns `fail` right away (no waiting for any progress). For `software_dev_full` this is fine — lint failure is non-gating there. `minimal_task` will fail loudly after 2 such iterations, which is the desired behaviour (no ralph fallback for trivial fanout).

## Notes for Review

- The `software_dev_full` build/test fan-out collects `lint_fut.wait()` but doesn't inspect the return value — so changing `lint`'s return type from `str` to `dict[str, Any]` is non-breaking there. `minimal_task` is the only consumer of the dict.
- The retired `verdicts/lint-iter-N.json` path: `software_dev_full` never read it; `minimal_task` did (now removed). No other code in either repo reads it (`grep -r "lint-iter-" --include='*.py'` post-change only returns the `output_files=[f"lint-iter-{iter_n}.log"]` log-publish line in `software_dev.py`, which is the lint *log* not the *verdict*).
