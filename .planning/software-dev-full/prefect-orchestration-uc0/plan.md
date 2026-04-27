# Plan — prefect-orchestration-uc0

`po run graph <root-id>` — generalize `epic_run` into an edge-driven sub-graph
fan-out that works on any bead root, regardless of dot-suffix naming or
`epic` status.

## Cross-repo note (self-dev issue)

PO's `epic` flow lives in the sibling formula pack at
`/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/`,
not in this rig. Per project `CLAUDE.md` ("**Do** land pack-contrib code in
the pack's repo … not in the caller's rig-path — see issue
`prefect-orchestration-pw4`"), the new `graph_run` flow + entry point go in
the pack repo, while a generic BFS/topo helper lives in this repo's core.

The bead's `po.pack_path` metadata is set to this rig (incorrect — it
mirrors `po.rig_path`), but the canonical pack location is the sibling
repo. Builder commits will need two `git add` / `git commit` calls — one
per repo — and the rig has no remote, so `git push` is a no-op there.

## Affected files

### Core — this repo (`/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration`)

- `prefect_orchestration/beads_meta.py` — add `list_subgraph(root, traverse, include_closed, include_root)` + `topo_sort_blocks(nodes)` helpers next to the existing `list_epic_children`. Edge-driven BFS via `bd dep list <id> --direction=up --type=<t> --json` (one shellout per (visited node × allowed edge type)); typed edges are read from each row's `dependency_type` field. The blocks-only sub-DAG used for topo order is built from `bd dep list <id> --direction=down --type=blocks --json` per collected node, intersected with the collected set. Cycle detection via `graphlib.TopologicalSorter` so the cycle members surface in the error.
- `tests/test_beads_graph.py` — new unit tests, mock `subprocess.run` to return synthetic `bd dep list` payloads. Covers BFS + edge-type filtering + closed-skip + `include_root` + cycle-error-shape.
- `CLAUDE.md` ("Common workflows" section) — document `po run graph` with one concrete example (a feature bead with sub-tasks linked via `bd dep`, fanned out in dep order).
- `engdocs/principles.md` (optional, only if a principle is touched) — likely no edit needed; the new verb fits §1 (PO wraps non-Prefect concept) + §2 (CLI-first).

### Pack — sibling repo (`/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas`)

- `po_formulas/graph.py` — new module. Defines `graph_run(root_id, rig, rig_path, traverse="parent-child,blocks", formula="software-dev-full", max_issues=None, include_closed=False, root_as_node=False, dry_run=False, **iter_caps) -> dict`. Internals:
  - Splits `traverse` on commas (string in, list out).
  - Calls core `list_subgraph(...)` then `topo_sort_blocks(...)`.
  - Resolves `--formula` to a callable via `importlib.metadata.entry_points(group="po.formulas")`.
  - Submits each node through a `_run_node_task` that calls the resolved formula (same shim pattern as `_run_issue_task` today).
  - Wires `wait_for=[futures[d] for d in node_blocks_deps if d in futures]`.
  - Same `_tag_epic_run`-style `issue_id:<root>` flow-run tagging (rename helper to `_tag_root_run` and reuse from `epic.py`).
- `po_formulas/epic.py` — `epic_run` becomes a thin wrapper: calls `graph_run` with `traverse="parent-child,blocks"`. Preserves the dot-suffix fallback inside `list_epic_children`/in the wrapper: if `list_subgraph(root)` returns 0 children, fall back to today's dot-suffix probe and feed the result through the same topo + submit code path.
- `pyproject.toml` — register `graph = "po_formulas.graph:graph_run"` in `[project.entry-points."po.formulas"]`.
- `tests/test_graph.py` — new unit tests with `StubBackend`/mocked discovery: assert (a) `--formula` dispatch resolves a non-default entry, (b) `--max-issues` caps after topo, (c) `wait_for=` is wired only with futures inside the collected set, (d) `--root-as-node` includes the root, (e) cycle in blocks-subgraph raises `ValueError` with a `dependency cycle: [...]` message.

### Rig — this repo (e2e)

- `tests/e2e/test_po_run_graph_cli.py` — opt-in (`PO_SKIP_E2E=1` is the rig default). Spawns a temp rig with `bd init`, creates 3 synthetic beads with explicit `bd dep` edges (no dot-suffix), runs `po run graph <root> --dry-run` (uses `StubBackend`), asserts all 3 are submitted in topo order and the verdict files exist. Run manually before declaring the bead done.

## Approach

1. **Lift the BFS + topo into core.** Today's `epic.py::_topo_sort` is duplicated logic that bakes the "epic children share a dot-suffix" assumption into the discovery layer. Splitting *traversal* (`list_subgraph`) and *ordering* (`topo_sort_blocks`) into `prefect_orchestration/beads_meta.py` makes them reusable by any future pack flow without importing from `po_formulas`.

2. **`bd` shape.** Confirmed against the live rig:
   - `bd dep list <id> --direction=up --json` → array of dependent issue rows (children whose blocker is `<id>`); each row has `dependency_type` (e.g. `"blocks"`).
   - `bd dep list <id> --direction=down --json` → array of issues that `<id>` depends on; each row has `dependency_type`.
   - `bd dep list` accepts `--type <t>` to pre-filter — preferred over in-process filtering so we don't pay for irrelevant rows.
   - `bd show <id> --json` returns a `dependencies[]` field, but its `dep_type` is `null` in this rig. **Use `bd dep list ... --json` for typed edge data**, not `bd show`.
   - For closed-status filter: `list_subgraph` checks each visited node's `status` (already in the dep-list rows) and skips `closed` unless `include_closed=True`.

3. **CLI parsing.** `po run` already passes arbitrary `--key value` kwargs through `_parse_kwargs` (`prefect_orchestration/cli.py:87`). `--traverse=parent-child,blocks` arrives as the string `"parent-child,blocks"`. Rather than teach `_parse_kwargs` about list coercion (a per-formula concern that breaks principle §1), `graph_run` splits its own `traverse` arg. Validates each token against `{"parent-child", "blocks", "tracks"}` and raises a `ValueError` listing the bad token if unknown.

4. **`--formula` dispatch (AC 4).** `graph_run` resolves the entry-point string at flow-body time:

   ```python
   from importlib.metadata import entry_points
   eps = {ep.name: ep for ep in entry_points(group="po.formulas")}
   if formula not in eps:
       raise ValueError(f"unknown formula {formula!r}; run `po list`")
   formula_callable = eps[formula].load()
   ```

   The submit shim accepts `formula_callable` as a kwarg. Contract for runnable formulas: must accept `issue_id`, `rig`, `rig_path` (and optionally `parent_bead`, `dry_run`). Documented in the docstring; a pre-flight `inspect.signature` check rejects formulas missing those keys with a clear error before any submissions land.

5. **Backward-compat for `epic` (AC 8).** Existing dot-suffix epics in this rig (e.g. `prefect-orchestration-3cu`) have **zero** explicit `bd dep` edges to their `<epic>.N` children — verified live: `bd dep list prefect-orchestration-3cu --direction=up --json` returns `[]`. A naive "delegate to graph_run" would silently drop every historical epic. Mitigation:

   - `epic_run` first calls `list_subgraph(epic_id, traverse=("parent-child","blocks"))`.
   - If that returns 0 nodes, falls back to the existing dot-suffix probe (`list_epic_children`'s current loop, kept intact).
   - Either way, results flow through the same `topo_sort_blocks` + submit-loop in `graph_run`. The wrapper passes the discovered node set into a new `graph_run`-internal entry point (`_dispatch_nodes(nodes, ...)`) so the discovery split is the only difference.

6. **Cycle detection (AC 2).** `graphlib.TopologicalSorter.static_order()` raises `CycleError` whose `args[1]` lists the cycle members. We catch it and re-raise:

   ```python
   raise ValueError(f"dependency cycle: {cycle_ids}")
   ```

   The message form is exact-match against AC (2). Cycle detection runs on the `blocks`-only sub-DAG (BFS via parent-child can't cycle — tree); `tracks`/`parent-child` cycles are not orderable but also not used for ordering, so they're tolerated.

7. **`--max-issues` (AC 5).** Applied to the topo-sorted list, taking the first N. This matches today's `epic_run` behavior. Documented as "topo prefix" in the flow docstring so callers don't expect leaf-first.

8. **`--root-as-node` (AC 7).** When set, the root is prepended to the collected node set (subject to the same closed-status filter — closed root is skipped unless `--include-closed` also set, conservative read of triage risk #3). `wait_for=` for the root is empty by definition (it has no in-set blockers).

9. **Documentation (AC 9).** `CLAUDE.md` "Common workflows" gets a new subsection "Running an arbitrary sub-graph" with two examples:

   - feature bead with sub-tasks linked by `bd dep add child --depends-on parent`
   - convoy / ad-hoc grouping bead

   Also a one-line note on the dot-suffix legacy fallback in the `Running an epic` subsection.

## Acceptance criteria

Verbatim from the issue:

1. `po run graph <root>` discovers all reachable non-closed descendants via allowed edge types and runs them as a DAG;
2. Refuses cleanly on cycle with `dependency cycle: [ids...]`;
3. `--traverse` accepts comma-separated edge type list; default = `parent-child,blocks`;
4. `--formula` selects which `po.formulas` entry point to run per node (default: `software-dev-full`);
5. `--max-issues N` caps how many are launched (after topo order);
6. `--include-closed` brings closed beads back into the set (for re-run / verification);
7. `--root-as-node` includes the root bead itself;
8. `po run epic X` still works and now delegates to graph traversal internally;
9. Documented in `CLAUDE.md` and engdocs with a concrete example (e.g., fanning out a feature bead's sub-tasks);
10. Verified live: run on an arbitrary root bead whose children have deps but no dot-suffix naming — all are picked up and ordered correctly.

## Verification strategy

| AC | How it's checked |
|----|-----------------|
| 1  | Unit test in core: mock `bd dep list` to return a 4-node graph; assert `list_subgraph` returns the 3 non-root non-closed nodes. Pack unit test: `graph_run` with `StubBackend` submits all 3 and the verdict files exist. |
| 2  | Pack unit test: build a 3-node graph with a cycle in `blocks`; assert `graph_run` raises `ValueError` whose `str(...)` matches the regex `^dependency cycle: \[.*\]$` and contains all cycle members. |
| 3  | Pack unit test: pass `traverse="parent-child"` only; assert `bd dep list` is invoked with `--type parent-child` and not `--type blocks`. Default-value test: omit `traverse`; assert both edge types queried. Bad-token test: `traverse="bogus"` raises a clear error. |
| 4  | Pack unit test: register a stub entry point in a temp `entry_points` patch, run `graph_run` with `--formula stub`, assert the stub callable is invoked once per node (not the default `software_dev_full`). Bad-name test: `--formula nonsense` raises with "unknown formula". |
| 5  | Pack unit test: 5-node topo, run with `max_issues=2`, assert exactly 2 submissions occur and the 2 are the topo-prefix. |
| 6  | Pack unit test: 1 closed + 2 open nodes; default run submits 2; `include_closed=True` submits 3. |
| 7  | Pack unit test: with `root_as_node=True`, the root appears in the submitted set with empty `wait_for=`; without it, the root is not submitted. |
| 8  | Pack unit test: `epic_run` with a synthetic dot-suffix-only epic (mocked `bd show <epic>.1/.2/.3`, empty `bd dep list --direction=up`) — assert the 3 children are still discovered and submitted. Existing tests in `software-dev/po-formulas/tests/` continue to pass unchanged. |
| 9  | Manual diff review: `prefect-orchestration/CLAUDE.md` adds the new subsection. |
| 10 | E2E: `tests/e2e/test_po_run_graph_cli.py` creates 3 synthetic beads in a temp rig with `bd dep add` edges (no dot-suffix), runs `po run graph <root> --dry-run`, asserts the run dirs for all 3 children exist after the flow returns. Plus a manual live run on a real non-dot-suffix grouping bead before closing. |

## Test plan

- **unit** (this repo, `tests/test_beads_graph.py` + pack `tests/test_graph.py`) — covers ACs 1–8. The bulk of coverage; mocks `subprocess.run` for `bd dep list` and uses `StubBackend` for the formula side.
- **e2e** (this repo, `tests/e2e/test_po_run_graph_cli.py`) — covers AC 10. Skipped by default in this rig (`PO_SKIP_E2E=1`); run manually with `uv run python -m pytest tests/e2e/test_po_run_graph_cli.py` before the bead is closed.
- **playwright** — N/A (no UI; `has_ui: false` per triage).

## Risks

1. **Dot-suffix legacy data.** Live-verified: existing `prefect-orchestration-3cu` family has zero `bd dep` edges to children. Switching `epic_run` to pure graph traversal would silently drop them. Mitigated by the wrapper's "0 children → fall back to dot-suffix" path. Critic should reject any version of `epic_run` that doesn't preserve this fallback.

2. **`dependency_type` value coverage.** Live data shows `"blocks"` only; `parent-child` and `tracks` are documented in `bd dep --help` but unobserved in this rig. If `bd dep list --type parent-child --json` returns rows with a different field name on a bd version we haven't tested, BFS will silently visit nothing. Build phase should add one synthetic `bd dep add` of each kind in the e2e test setup to confirm the field shape.

3. **`bd dep list` shellout cost.** One `bd` invocation per (visited node × allowed edge type) for BFS, plus one per collected node for the blocks sub-DAG. For an N=20 graph with 2 edge types that's ~60 shellouts (~60–120 ms each) → ~5–10 s. Acceptable for now. If it bites, batch via `bd dep list a b c --json` (the CLI accepts multiple IDs and emits a flat array). Defer the optimization unless we measure pain.

4. **Concurrency on dolt-server.** This rig is on dolt-server (`.beads/dolt-server.port` exists); parallel `bd update --claim` from each spawned formula is safe. The new `graph_run` flow body itself doesn't write to bd directly — `software_dev_full` does the claims. Add a runtime warning if `auto_store(...)` detects `embedded` mode and the graph has >1 fan-out node, mirroring the `po doctor` `check_beads_dolt_mode` lint.

5. **Formula contract drift.** AC (4) lets `--formula` pick *any* `po.formulas` entry. If a formula doesn't accept `(issue_id, rig, rig_path)`, dispatch fails late. Pre-flight `inspect.signature` check rejects mis-shaped formulas before any submissions. Documented in the `graph_run` docstring as "formula contract".

6. **CLI `--traverse` parsing.** `_parse_kwargs` doesn't split on commas — `graph_run` does it itself. If a user types `--traverse=blocks --traverse=parent-child` (two flags), the second clobbers the first (Typer's normal behavior). Documented; not an AC concern.

7. **No git remote on this rig.** Builder commits land in the pack repo (which has its own remote) and in this rig (local-only). `git push` will be a no-op for the rig — not a behavioral risk, just a process note.

8. **Beads tag/metadata flow-run wiring.** `epic_run` stamps `epic_id:<id>` and `issue_id:<id>` tags via `_tag_epic_run`. `graph_run` should stamp `root_id:<id>` and `issue_id:<id>` instead (or `graph_root:<id>`); the `epic` wrapper continues to stamp `epic_id:<id>` for backward compat with anything that filters on it (likely nothing — verify via grep before renaming). `po status` groups by `issue_id:` only, so the secondary tag is informational.
