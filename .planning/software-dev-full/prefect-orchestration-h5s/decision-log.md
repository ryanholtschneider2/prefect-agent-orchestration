# Decision log — prefect-orchestration-h5s (build iter 1)

- **Decision**: Extended the existing `list_epic_children` with a
  `mode={"ids","deps","both"}` parameter rather than adding a new
  function (`list_epic_children_modes`).
  **Why**: The bead text explicitly asks "make `list_epic_children`
  also walk `bd dep`" — extension is the requested API. A new
  function would have left two near-identical primitives and forced
  every caller to pick one. Default `mode="ids"` keeps the function
  back-compat for any external caller that imports without
  thinking about modes.
  **Alternatives considered**: Add `list_epic_children_via_deps()`
  alongside the existing function (rejected — duplicates state, drifts
  over time); make the function dispatch-only and put the dot-suffix
  loop in a new `_dot_suffix_children` (chosen for the helper but the
  public surface is still the modal `list_epic_children`).

- **Decision**: `list_epic_children` now returns the unified
  `{id, status, title, block_deps}` shape for **all** modes (matching
  `list_subgraph`).
  **Why**: Callers want the same shape regardless of how children were
  found — they immediately feed `topo_sort_blocks`. Returning the raw
  `{..., dependencies: [...]}` shape for `mode="ids"` and the graph
  shape for `mode="deps"` would have forced every consumer to branch.
  **Alternatives considered**: Keep `mode="ids"` returning the legacy
  shape for byte-for-byte back-compat. Rejected because the only
  in-tree consumer (`po_formulas.epic._legacy_dot_suffix_children`)
  was already adapting on each call — moving the adapter into core is
  cleaner.

- **Decision**: Built `collect_explicit_children(child_ids)` as a
  separate top-level helper rather than another `mode` of
  `list_epic_children`.
  **Why**: `--child-ids` doesn't take an `epic_id` (the user supplies
  the set directly). Putting it under `list_epic_children` would have
  meant ignoring `epic_id` for that mode — confusing API. Two
  functions with single responsibilities read better.
  **Alternatives considered**: `list_epic_children(epic_id, mode="explicit",
  child_ids=...)`. Rejected — `epic_id` would become unused for one
  mode and the signature gets messy.

- **Decision**: `--child-ids` rejects closed beads with a clear error
  (rather than silently skipping).
  **Why**: Consistent with `list_epic_children`'s
  `include_closed=False` semantics, and a user who lists a closed bead
  almost certainly meant to reopen it. Silent skipping would dispatch
  fewer beads than the user expected without warning.
  **Alternatives considered**: Drop closed ids and warn. Rejected —
  the explicit-list path is small and dispatch behaviour should be
  predictable; surfacing the error gives the user a clear next step
  (reopen the bead).

- **Decision**: Dot-suffix probe stays as the **default** mode for
  `list_epic_children` (i.e. `mode="ids"`), but `epic_run`'s
  `discover` parameter defaults to `"both"`.
  **Why**: Two different defaults serve different audiences. The
  function's default preserves the historical API contract for any
  external caller. The CLI flag's default is the "do what I mean"
  behaviour the issue asks for — pick up dot-suffix children *and*
  bd-dep-linked children without the user knowing which the epic uses.
  **Alternatives considered**: Make both defaults `"both"`. Rejected
  — silently changing the function's default would be a surprise for
  any external caller that omitted `mode=`.

- **Decision**: Updated `tests/test_epic_legacy_dot_suffix.py` rather
  than keeping it byte-for-byte.
  **Why**: The original test patched `list_epic_children` to return
  the legacy `{..., dependencies: [...]}` shape and then asserted the
  shim adapted it. After consolidating the adapter into core, the
  shim is a one-line passthrough — the test now pins the new
  passthrough contract (and verifies `mode="ids"` is the call mode).
  **Alternatives considered**: Keep the old test passing by leaving
  the adapter in `_legacy_dot_suffix_children`. Rejected — that would
  duplicate the now-canonical adaptation logic in two places.

- **Decision**: Did not add a `--discover` Typer option to `cli.py`.
  **Why**: `po run` already passes through unknown `--key value` pairs
  to the formula via `_parse_kwargs`; adding the param to the
  `epic_run` signature is enough to surface it on the CLI without
  changes to core. Keeps core formula-agnostic per the principles doc.
  **Alternatives considered**: Add explicit Typer args. Rejected —
  ties core to specific pack params, contradicts §1 "core is
  pluggable".

- **Reminder for handoff**: After landing, run `po packs update` so
  the new `epic_run` params (`discover`, `child_ids`) appear in
  `po show epic` (entry-point metadata is baked at install time, not
  on code reload).
