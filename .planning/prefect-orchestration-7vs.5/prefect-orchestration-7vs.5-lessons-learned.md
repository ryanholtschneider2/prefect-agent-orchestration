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

## Code-Review Notes (2026-04-28)

- **Issue** — Reviewer needed to trace the contract between
  `per_role_step` and the legacy `@task` callables it dispatches.
  This crosses two repos and ~10 files; the implementation summary
  did not call out the role-step-bead-closure semantics, and the
  decision log did not address it. The CRITICAL gap (legacy tasks
  close `<seed>.lint.<n>` etc., not the role-step bead `<seed>.
  role.linter.iter<n>` that graph_run is awaiting) only surfaces by
  reading both per_role_step.py and the bodies of `lint`,
  `critique_plan`, etc.
- **Resolution** — Returned MAJOR_REVISION with explicit BLOCKING
  finding on the closure gap and on the silent `_MAX_PASSES`
  success-close behaviour.
- **Recommendation** — Future graph-mode work should require an
  integration-style test that drives at least one role-step
  through `per_role_step` against a temp `bd init` rig and
  asserts the role-step bead is closed. The StubBackend test
  deferral in this issue is exactly what allowed the contract gap
  to ship.

## Code-Review iter 2 fixes (2026-04-28)

- **Issue** — Iter 1 had `per_role_step` auto-closing the role-step
  bead AFTER the @task returned. This was wrong: graph-mode design
  is "agent closes its own bead" so verdict-decoding (clean/failed,
  approved/rejected, passed/failed) flows from the closure reason
  the AGENT writes. Auto-closing erased that signal.
  **Resolution** — Reverted to agent-driven closure. Added a
  defensive force-close belt that fires only when the bead is still
  open after the @task returns, with a sentinel `notes="agent did
  not close role-step bead"`. Each prompt now carries a
  `{{role_step_close_block}}` injection with role-specific reason
  guidance (centralised in `_ROLE_CLOSE_GUIDANCE`).
  **Recommendation** — When migrating role tasks to bead-mediated
  handoff, ALWAYS read the existing 7vs.3 lint + 7vs.4 critic
  patterns first. Both already use agent-driven closure with
  keyword-decoded verdicts. The first-iter mistake was treating
  per_role_step as if it owned the close; it doesn't — it owns
  the dispatch + force-close defense, nothing else.

- **Issue** — Iter 1 had `critique_plan` / `review` / `lint` calling
  `create_child_bead` even in graph mode, minting a duplicate iter
  bead alongside the orchestrator-seeded role-step bead. The agent
  then closed one of them and graph_run waited on the other.
  **Resolution** — Detect graph mode via `ctx.get("role_step_bead_id")`;
  reuse the seeded bead instead of creating a new one. Legacy
  mode (no role_step_bead_id) keeps the original behavior.
  **Recommendation** — When two layers can author beads with similar
  shapes, the contract for "who creates" must be unambiguous. Here
  the seed graph creates iter beads in graph mode; the legacy task
  creates them in legacy mode. Adding a single sentinel ctx flag
  (`role_step_bead_id`) was the cleanest discriminator.

- **Issue** — `_MAX_PASSES` exhaustion silently `bd close`d the seed
  bead with the same `complete` notes as a successful run, masking
  runaway loops as success.
  **Resolution** — Return a distinct status; leave the seed open so
  `bd ready` keeps surfacing it.
  **Recommendation** — Failure paths must NEVER share their close
  semantics with success paths. Idempotent `bd close` is a footgun
  here: closing on failure looks identical to closing on success
  unless the `notes` differ. Better: don't close on failure at all.

- **Issue** — Cap-exhaustion closed the over-cap iter bead but left
  its downstream blocks-edge subtree open, so `_MAX_PASSES` was
  always invoked at least once even when caps fired correctly.
  **Resolution** — `_close_subtree_blocks_down` BFSs and propagates
  `cap-exhausted: …` down the subtree.
  **Recommendation** — When a node's failure invalidates its
  descendants, model that explicitly. The graph_run frontier
  semantics ("close it and dependants drop") works by closing
  EVERYTHING that's blocked, not just the proximate cause.

- **Issue** — Critic ctx had no rebuilt iter description in graph
  mode, regressing the 7vs.4 iter-self-sufficiency contract.
  **Resolution** — `_rebuild_critic_iter_context` reconstructs from
  prior critique markdown + seed-bead title.
  **Recommendation** — Cross-task state in graph-mode must be
  reconstructed from durable artifacts (run_dir files, bead
  metadata) — Python scope doesn't exist across Prefect task
  boundaries. Audit every `ctx[...]` consumer in legacy code and
  ensure graph mode produces the same value from disk.
