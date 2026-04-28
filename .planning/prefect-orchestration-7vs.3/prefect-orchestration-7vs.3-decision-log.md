# Decision Log: prefect-orchestration-7vs.3

**Decision**: Treat still-open lint bead as `fail` (verdict dict), not raise.
**Why**: Plan §Decision points; `software_dev_full` doesn't gate on lint, so raising would change behaviour outside this issue's blast radius.

**Decision**: `_read_lint_verdict` checks `closure_reason` THEN falls back to `reason`.
**Why**: Different bd JSON dialects. Plan said "closure_reason (or reason)"; covering both is cheap.

**Decision**: Failure summary = first non-empty line of `notes`, fall back to `reason`, fall back to literal `"lint failed"`.
**Why**: Notes can be multi-line (agent ran `bd update --append-notes` multiple times). Legacy verdict contract was a one-liner; preserve that shape.

**Decision**: Added a 4th test (`test_create_child_bead_idempotent_on_already_exists`) beyond the plan's 3.
**Why**: Idempotency branch isn't reached by the lint() flow tests (no collision in those paths). Direct test is cheap insurance against regression on the `"already exists"` substring match.

**Decision**: Used existing module-level `_bd_show` (technically a private name) rather than promoting it to public.
**Why**: It's already used by other helpers in the same module; promoting requires a separate API decision and isn't needed for this pilot.

**Decision**: `create_child_bead` raises `RuntimeError` (not silently swallows) on non-`already-exists` non-zero exits.
**Why**: Silent failure would leave the lint task with no child bead and a downstream `_bd_show` "not found" → `fail` verdict that hides the real problem. Loud failure is consistent with the rest of beads_meta (`watch` raises on bad input, `collect_explicit_children` raises on closed/missing, etc.).

**Decision**: Dropped `run_dir` local from `minimal_task` flow body when removing the `read_verdict` call.
**Why**: It became unused. Leaving dead locals hides intent. `_load_rig_env` still uses `Path(rig_path)` so the `Path` import stays.

**Decision**: Pack test goes in `tests/` (root), not `tests/e2e/`.
**Why**: Project CLAUDE.md — test layers must not overlap. The new test monkeypatches `subprocess.run`; no real bd binary, no Prefect server. That's unit-layer by definition.
