# Lessons Learned: prefect-orchestration-7vs.2

## Planning Difficulties

- **Issue**: Initial read of the codebase suggested role→uuid was always
  written to `<run_dir>/metadata.json` (per the `sessions.py` docstring
  comment). In fact, `beads_meta.auto_store(parent_id=...)` already routes
  writes to `BeadsStore` on the parent bead when `parent_bead` is supplied —
  so epic/graph runs already accumulate sessions on the parent. The actual
  gap is narrower than the issue body implied.
- **Resolution**: Re-read `auto_store` + the call sites in `epic.py`,
  `graph.py`, `software_dev.py`. Re-scoped the plan: the new
  `RoleSessionStore` reuses BeadsStore as the primary tier (no behavior
  change for epic/graph) and adds (i) seed-bead resolution for solo
  child runs and (ii) a JSON file fallback + legacy migration shim.
- **Recommendation**: When a "missing feature" bead seems large, grep
  call sites of the central abstraction (`auto_store`, `BeadsStore`)
  before scoping. Some of the work may already be done implicitly via a
  parameter that's not always set.

## Implementation Difficulties

- **Issue**: Existing `test_role_registry.py::test_role_registry_cwd_routing`
  constructs `RoleRegistry` directly with only a `FileStore` (no
  `role_session_store`). Naively rewiring `get`/`persist` to *require*
  `role_session_store` would break this fixture and any external direct
  constructors.
- **Resolution**: Added defensive `_read_session`/`_write_session`
  indirections in `RoleRegistry` that fall back to
  `store.get/set("session_<role>")` when `role_session_store is None`.
  Production callers (`build_registry`) always wire the new store; the
  fallback is purely for direct-construction back-compat.
- **Recommendation**: When adding a new optional field that supplants
  an existing dependency, keep the old path as a graceful fallback —
  even if no production caller exercises it — so test fixtures and
  ad-hoc external callers don't break silently.

- **Issue**: `load_role_sessions` could plausibly return either bare-role
  keys or prefixed `session_<role>` keys. Plan amendment 2 explicitly
  picked prefixed; without that, `build_rows` (which strips the prefix
  via `_role_from_key`) would have silently filtered everything out.
- **Resolution**: Confirmed `sessions.build_rows`/`lookup_session`
  expect the prefixed shape; converted at the boundary in
  `load_role_sessions`. `RoleSessionStore` internal API stays
  bare-role.
- **Recommendation**: When a function fans out to existing helpers,
  trace the key shape they consume *before* writing the new function.
  A 3-minute grep saves a debug session.

## Testing & Verification Difficulties

- **Issue**: `bd dep list --direction=down --type=parent-child` direction
  semantics are not unambiguously documented in this repo's engdocs;
  whether down means "what depends on me" (descendants) or "what I
  depend on" (ancestors) decides whether the seed walker traverses
  child→parent or the reverse.
- **Resolution**: Promoted "verify direction" to mandatory implementation
  step 1 with a one-shot probe before any code lands. Encoded the
  verified direction in the docstring of `resolve_seed_bead`.
- **Recommendation**: Treat any `bd dep` traversal direction as
  pre-flight verification material — never assume from the flag name.

## Documentation Difficulties

- No significant difficulties encountered. `engdocs/principles.md`
  cleanly addresses the "is this a primitive or composition?" question
  for §5; `engdocs/primitives.md` row 26 already names per-role session
  resume as a covered primitive being tightened, not a new addition.

## General Lessons & Follow-Ups

- **Issue**: No external plan-reviewer agent was spawnable in this
  planning environment (the orchestrator harness expects a sub-agent
  spawn but no Task/Agent tool was available in the toolset).
- **Resolution**: Performed an explicit self-critique pass after writing
  the initial plan; surfaced 5 amendments, folded them into the plan,
  documented the review in the plan's "Review History" section.
- **Recommendation**: When the spawn primitive isn't available, make the
  self-critique explicit and in-band so the human reviewer (and any
  downstream builder agent) can see review actually happened, what was
  found, and what changed.

- **Follow-up bead candidate**: instrument
  `AgentSession._last_was_fork` and emit a DeprecationWarning when
  `RoleRegistry.persist(role)` is called immediately after a forked
  turn. Captured under "Risks" in the plan as a forward-looking item;
  not blocking 7vs.2 acceptance criteria.

## Orchestrator notes

- bd-dep direction (`--direction=down --type=parent-child` returns parents from a child) is non-obvious and was caught only by the plan's mandatory empirical pre-flight. Worth canonicalizing in `engdocs/` if more callers need this walk.
- Reviewer flagged a behavior change worth noting: solo `po run` (with bd on PATH) now writes role sessions to `BeadsStore` instead of `<run_dir>/metadata.json`. Aligned with the goal (single source of truth via bd) and migration shim keeps reads compatible, but it does mean `po retry --keep-sessions` becomes a no-op for new runs. Help-text was updated.
- Solo run case has `seed_run_dir == legacy_self_run_dir` which means the JSON file and the legacy file coexist in the same dir; not a bug (different filenames) but worth a comment in role_sessions.py if it becomes confusing.
