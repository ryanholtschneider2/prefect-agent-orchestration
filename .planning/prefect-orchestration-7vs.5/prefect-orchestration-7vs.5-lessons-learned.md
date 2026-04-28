# Lessons Learned — prefect-orchestration-7vs.5 (planning phase)

- **Issue.** `create_child_bead` accepts only a single `blocks` id, but the
  seed graph for software_dev_full has fan-in nodes with 3+ blockers
  (e.g. `tester-diff.iter1` waits on lint + unit + e2e).
  **Resolution.** Plan calls for chaining `bd dep add` after the
  `create_child_bead` call rather than extending the helper signature
  (smaller blast radius for this issue; follow-up bead can land the
  ergonomic improvement).
  **Recommendation.** When designing primitives, sanity-check against
  the most fan-in-heavy real graph before locking the signature.

- **Issue.** `child_ids=` looked like it was on `graph_run` (because epic_run
  documents it) but is actually only on `epic_run`. Initial plan skeleton
  assumed it on graph_run.
  **Resolution.** Read `graph.py` directly, confirmed kwarg is missing,
  and rewrote the seed flow to rely on `list_subgraph(..., include_closed=False)`'s
  natural frontier filtering — which is the cleaner design anyway (no
  Python-side `dispatched` set needed).
  **Recommendation.** When two flows share docs, verify by reading the
  signature, not the docstring.

- **Issue.** No friction beyond the above. Existing primitives
  (`watch()`, `list_subgraph`, `topo_sort_blocks`, `RoleSessionStore`,
  `create_child_bead`, `graph_run`) compose to deliver Scope B without
  any new core primitive — gratifying confirmation of `principles.md` §5.

# Lessons Learned — implementation phase

- **Issue.** Plan skeleton showed `software_dev_full(issue_id, rig,
  rig_path, **kwargs)`; this regressed two existing tests
  (`test_software_dev_full_accepts_pack_path_kwarg`,
  `test_software_dev_full_accepts_gate_iter_cap_kwarg`) AND would
  have failed `graph.py::_check_formula_signature` validation that
  named params include `issue_id`, `rig`, `rig_path`.
  **Resolution.** Restored the explicit kwargs signature on the
  dispatcher (28 lines instead of 10); graph-mode body stayed at 78
  LOC, comfortably under the 100 budget.
  **Recommendation.** When a plan calls for "thin dispatcher",
  enumerate the existing introspection / validation sites first.
  Pre-existing tests assert the public signature; flow validators
  reject `**kwargs`-only formulas.

- **Issue.** `claim_issue` requires an `assignee` parameter, but the
  plan skeleton called `claim_issue(issue_id, rig_path=...)` with no
  assignee.
  **Resolution.** Used `build_registry(claim=True)` once at the top
  of `_graph_software_dev_full` — `build_registry` already wraps
  `claim_issue` with the `po-<flow_run_id>` assignee logic and also
  creates the run_dir + stamps metadata. This collapses three tasks
  into one call and matches what legacy does at the same point.
  **Recommendation.** When composing existing primitives, prefer the
  fully-bundled bootstrap (`build_registry`) over the underlying
  helpers (`claim_issue` directly).

- **Issue.** StubBackend graph-mode integration test was deferred. The
  existing pack tests (`test_software_dev_critic_bead.py`) mock at the
  `_bd_show` level — they don't actually run `graph_run` against a
  rig. Building a faithful in-process harness for graph mode would
  require either (a) inventing a `bd init`-like temp-rig fixture or
  (b) deeply mocking `list_subgraph` / `topo_sort_blocks` /
  `create_child_bead` / `_run_node_task.submit`. Neither felt like
  net-positive work at this scope.
  **Resolution.** Documented as deferred in implementation summary;
  acceptance criterion (a) is covered by the verifier role's manual
  Claude run on a tiny throwaway issue.
  **Recommendation.** Plans should mark integration-test infra work
  with a separate budget; "if time permits" naturally resolves to
  "deferred" without a clear stop condition.

- **Issue.** Dolt-server backend was unfamiliar — would per_role_step's
  parallel `bd update --metadata` calls collide?
  **Resolution.** The rig is already on dolt-server (per CLAUDE.md
  "Backend (dolt-server)"); legacy lint ∥ unit ∥ e2e parallelism
  already exercises the same concurrency. No change needed.
  **Recommendation.** When parallelism scope changes, sanity-check
  what the existing legacy already does in parallel — usually you're
  not actually expanding the contention surface.
