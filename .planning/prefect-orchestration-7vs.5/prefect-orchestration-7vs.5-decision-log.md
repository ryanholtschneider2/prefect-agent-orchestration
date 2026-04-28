# Decision Log — prefect-orchestration-7vs.5

The plan settled the 9 hard design questions; this log captures only
DEVIATIONS from the plan and decisions on points the plan did not fix.

- **Decision**: Place `_graph_software_dev_full` and `ROLE_TASKS` in
  `software_dev.py`; place `seed_initial_graph` and `per_role_step` in
  separate modules.
  **Why**: Matches the file-modification table in the plan ("Files to
  Modify" §); keeps the legacy body intact behind the `_legacy_*` rename
  while exposing the seed flow's helpers as importable units for unit
  tests.

- **Decision**: `_legacy_software_dev_full` is the verbatim-renamed
  current body; `software_dev_full` becomes a 4-line dispatcher.
  **Why**: Plan §"Implementation Steps" item 3 + acceptance criterion
  (b) require byte-for-byte legacy preservation.

- **Decision**: Bead-id naming uses `<seed>.role.<role>.iter<N>` /
  `<seed>.role.<role>` per plan §"Seed bead structure".
  **Why**: The plan uses dashes for compound role names (e.g.
  `tester-baseline`, `releaser-deploy-smoke`) and keeps `iter<N>` only
  on roles that legacy iterates on.

- **Decision**: `_parse_role_from_bead_id` accepts ids of the form
  `<seed>.role.<role>[.iter<N>]` and strips the optional iter suffix.
  Role keys may contain hyphens (`tester-baseline`) but never dots.
  **Why**: Bead ids use dots as separators; role names use dashes per
  the plan; this gives a clean parse rule without having to know the
  full ROLE_TASKS keyset.

- **Decision**: `seed_initial_graph` uses `create_child_bead(...,
  blocks=<first>)` then shells `bd dep add <child> --type blocks --to
  <other>` for additional blockers, NOT extending `create_child_bead`.
  **Why**: Plan §"Multi-blocker beads" explicitly defers helper-
  signature changes to a follow-up; lessons-learned (planning phase)
  flagged this as deliberate.

- **Decision**: `_graph_software_dev_full` calls `graph_run` directly
  via the imported callable, not via the EP indirection.
  **Why**: Same Python process; EP lookup would just resolve to the
  same function and add unnecessary indirection. `graph_run` itself
  resolves the *node-level* formula (`per-role-step`) by EP name —
  that's the level where indirection earns its keep.

- **Decision**: Skip the StubBackend integration test in this
  implementation pass; ship `test_graph_mode_dispatcher_picks_branch`
  and `test_graph_loc_under_100` only.
  **Why**: A faithful Stub-driven graph-mode test requires running
  Prefect's `graph_run` against a temp-rig with `bd init` + a fake
  `per_role_step` mock, which is non-trivial harness work the plan
  marked as "if time permits". The dispatcher-branch + LOC tests
  cover acceptance criterion (c) directly; verification will catch
  end-to-end regressions on the manual `7vs.5.demo` run per the
  plan §"Verification Strategy" row (a).

- **Decision**: `_load_rig_env` (currently private to `software_dev.py`)
  is accessed by `_graph_software_dev_full` via direct call; it's not
  re-imported by `per_role_step.py` (which gets the same effect via
  `build_registry`'s sequencing).
  **Why**: Single source of truth for env loading; the env vars persist
  in the process for the duration of the flow.

- **Decision**: `_enforce_caps` is a thin per-role iteration counter
  that closes-with-cap-exhausted any iter bead whose `iterN` exceeds
  its cap. It is run BEFORE each `graph_run` pass.
  **Why**: Plan §"Cap exhaustion" calls for orchestrator-side cap
  policing; closing the bead before dispatch makes `list_subgraph(...,
  include_closed=False)` skip it automatically with no Python-side
  bookkeeping (per the plan's revised approach in
  §"Questions and Clarifications" #3).

- **Decision**: `per_role_step` does NOT call `claim_issue` for the
  iter beads themselves; only the seed (input) bead is claimed by the
  outer dispatcher.
  **Why**: Iter beads are orchestrator-internal; claiming each one
  would generate noise in `bd ready` for parallel humans. The
  per-role `@task` it dispatches closes the iter bead via the agent's
  `bd close <iter-id>` call as today.

- **Decision**: `software_dev_full` flow signature unchanged.
  **Why**: Acceptance (b) requires legacy parity; epic_run / graph_run
  callers pass through every kwarg today, and the dispatcher branches
  on env var, not on a new param.
