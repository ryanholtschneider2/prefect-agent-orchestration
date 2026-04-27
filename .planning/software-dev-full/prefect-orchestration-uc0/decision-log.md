# Decision log — prefect-orchestration-uc0

## Build iter 2 — responses to critic

- **Decision**: Switch CLI surface from positional `po run graph
  <root-id>` to flag form `po run graph --root-id <id>`.
  **Why**: critic blocker. `cli.py::run` consumes the first non-option
  token after `run` as the **formula name** and feeds the rest to
  `_parse_kwargs`, which rejects bare positional tokens with
  `BadParameter("expected --key, got 'my-issue-1'")`. The e2e test
  would have fired exactly that error on its first `po_runner` call.
  Flag form mirrors the existing `--epic-id` / `--issue-id` pattern
  used everywhere else in PO. Updated CLAUDE.md (running-a-sub-graph
  examples + the verb table) and the e2e test to match.
  **Alternatives considered**: teaching `cli.py::run` to pass through
  a *second* non-option token as the first positional kwarg of the
  formula — rejected, principle §1 (don't add CLI plumbing for one
  formula's sake) plus it would surprise every other formula that
  doesn't expect a positional.

- **Decision**: Documented BFS-through-closed semantics in
  `list_subgraph`'s docstring.
  **Why**: critic nit #3. The implementation already does the right
  thing (BFS visits closed intermediates, status filter prunes only
  the final collected set), but the contract was implicit. One
  sentence makes "re-run the open tail of a half-finished chain"
  legible to future readers.

- **Decision**: Hoisted the closed-root short-circuit above the
  `_bd_show` shellout in `list_subgraph`.
  **Why**: critic nit #4. When `include_root=True` and the root is
  closed and `include_closed=False`, the row was fetched and then
  thrown away. Now the closed root path is detected and skipped (or
  the row is fetched only when it might be kept). Trivial saving but
  the cost was zero.
  **Alternatives considered**: deferring per the critic's "if you
  care" — fixed because the change is one if-statement.

- **Decision**: Did NOT relax `_check_formula_signature` to accept
  `**kwargs`-only formulas (critic nit #5).
  **Why**: today's two registered formulas (`software-dev-full`,
  `graph`) both declare named params; relaxing the check would weaken
  the pre-flight error for the contract violation we actually expect
  (a formula that genuinely doesn't accept `(issue_id, rig, rig_path)`).
  Updated the `graph_run` docstring to call out the stricter contract
  ("must declare these as named parameters; `**kwargs` is not
  sufficient") so the surprise is documented rather than implicit.
  **Alternatives considered**: `if any(p.kind == VAR_KEYWORD for p in
  sig.parameters.values()): return` — rejected, weakens the error for
  the real failure mode.

- **Decision**: Captured e2e AC-10 verification by running the live
  test with `PO_SKIP_E2E` unset and pasting output into
  `final-tests.txt`.
  **Why**: critic nit #6 — the prior `final-tests.txt` was empty, so
  the regression gate had no record of what state the suite was in.
  This run is the canonical AC-10 evidence.

## Build iter 1

- **Decision**: Cross-repo edits — core helper goes in this rig
  (`prefect_orchestration/beads_meta.py`), pack code goes in the sibling
  repo (`/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/`).
  **Why**: the actual `po_formulas` package lives in the sibling pack repo
  (verified: no `po_formulas/` exists in this rig). The bead's
  `po.pack_path` metadata mirrors `po.rig_path`, which the build prompt
  relayed verbatim — but the project `CLAUDE.md` rule "**Do** land
  pack-contrib code in the pack's repo … not in the caller's rig-path"
  (per `prefect-orchestration-pw4`) is the canonical convention. Plan §
  "Cross-repo note" already flagged this. Two `git add` / `git commit`
  pairs, one per repo.
  **Alternatives considered**: (a) put pack code under `po_formulas/` in
  this rig — rejected, the installed pack via `entry_points()` would
  still resolve to the sibling, and the rig would carry duplicate / dead
  code. (b) move the pack into this rig — out of scope, separate
  refactor, and would orphan the pack's own git history.

- **Decision**: Skip mcp-agent-mail file reservations.
  **Why**: the `mcp-agent-mail` tool family is not currently exposed in
  this session (ToolSearch returns no match). Per the build prompt's
  fall-through guidance ("on crash, reservations auto-expire; no manual
  cleanup needed"), proceeding without reservations is acceptable for a
  single-worker run. Concurrent worker collisions remain possible but no
  other PO worker is on these paths in the current rig (`po status`
  shows only this issue running).
  **Alternatives considered**: block on the missing tool — rejected, the
  bead has been claimed and the run is in flight; aborting would leave
  the work in a worse state.

- **Decision**: BFS via per-(node, edge-type) `bd dep list` shellouts;
  no batching.
  **Why**: plan §"Risks" item 3 — for typical N≤20 graphs the cost is
  ~5–10 s of shellout overhead, comfortably below the per-formula
  Claude-call cost. Batching via `bd dep list a b c d --json` is a
  premature optimization until measured.
  **Alternatives considered**: single `bd list --json` of the whole rig
  followed by in-process filtering — rejected, that loads every bead in
  the rig (1000s) into memory and doesn't expose typed edges from the
  `dependencies[]` field (its `dep_type` reads as `null` in this rig).

- **Decision**: `dependency_type` filter via `--type <t>` flag on `bd
  dep list`, not via in-process filtering of an unfiltered call.
  **Why**: bd already supports the flag (`bd dep list --type tracks`)
  and pre-filtering at the bd layer halves the rows we deserialize when
  the user only wants one edge type. Also future-proofs against new
  edge types we don't enumerate in `_VALID_EDGE_TYPES`.
  **Alternatives considered**: fetch all edges per node, filter in
  Python — rejected for the reason above.

- **Decision**: `epic_run` keeps the existing `list_epic_children`
  dot-suffix probe as a *fallback* when `list_subgraph` returns 0
  children, instead of rewiring it to depend purely on `bd dep` edges.
  **Why**: live-verified that `prefect-orchestration-3cu` (a real epic
  in this rig) has zero `bd dep` edges to its `<epic>.N` children.
  Switching the wrapper to pure graph traversal would silently drop
  every historical epic. Plan risk #1.
  **Alternatives considered**: backfill `bd dep` edges for every legacy
  dot-suffix epic — out of scope, not the focus of this bead.

- **Decision**: Reuse the `epic_run` topo + submit body by extracting
  `_dispatch_nodes(nodes, …)` in `graph.py` and importing it from
  `epic.py`. Keep `_run_issue_task` task-name as-is in epic.py; add a
  parallel `_run_node_task` in graph.py that takes a `formula_callable`
  kwarg.
  **Why**: minimizes diff to `epic.py`'s shape (the wrapper still calls
  a Prefect task, so concurrency-limit tags / wait_for= behave
  identically). The new task can't reuse `_run_issue_task` because that
  one hard-imports `software_dev_full`; `graph_run` needs late-binding
  via the `--formula` arg.
  **Alternatives considered**: parametrise `_run_issue_task` to accept
  any callable — rejected, Prefect tasks can be tricky to introspect
  with non-pickleable callables passed as kwargs; cleaner to have one
  task per dispatch shape.

- **Decision**: Stamp `root_id:<id>`, `issue_id:<id>` tags on the
  `graph_run` flow run. Continue stamping `epic_id:<id>` from the
  `epic_run` wrapper for backward compat.
  **Why**: `po status` groups by `issue_id:<id>`. The `epic_id:` tag is
  legacy but might still be used by external tooling I haven't
  inventoried; keeping it on the epic-shaped invocation is cheap.
  **Alternatives considered**: drop `epic_id:` tag — rejected, no
  upside, real backward-compat risk.
