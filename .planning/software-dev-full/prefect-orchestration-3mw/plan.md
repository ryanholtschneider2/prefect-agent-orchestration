# Plan — prefect-orchestration-3mw

## Affected files

- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/beads_meta.py`
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/role_registry.py`
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/run_handles.py`
  (one shellout — `stamp_run_url_on_bead` — is invoked from `build_registry`
  and inherits the same cwd bug; thread `rig_path` in.)
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_beads_graph.py`
  (extend the existing fake-bd fixture to capture `cwd=` kwargs and
  add cwd-propagation assertions; no new file needed for the
  graph-traversal coverage.)
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_beads_meta_cwd.py`
  *(new)* — focused unit tests for `BeadsStore`, `claim_issue`,
  `close_issue`, `_bd_show`, `_bd_dep_list`, `collect_explicit_children`,
  and `list_epic_children` cwd propagation; plus list-vs-dict shape
  defense for `BeadsStore.get` / `BeadsStore.all`.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_role_registry.py`
  (extend `test_build_registry_dry_run` and add a non-dry-run case that
  monkeypatches `subprocess.run` to assert `cwd=rig_path` is passed to
  every shellout `build_registry` issues — `bd update --set-metadata`,
  `_resolve_pack_path`'s `bd show`, `_resolve_tmux_scope`'s `bd show`,
  `claim_issue`, and `stamp_run_url_on_bead`'s `bd update`. Also assert
  the constructed `BeadsStore` carries `rig_path`.)
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/e2e/test_epic_dispatch_cwd.py`
  *(new, gated)* — provisions a temp rig in `tmp_path` with `bd init`,
  creates an epic + 3 parent-child children, invokes `epic_run` with
  `--dry-run` from a Python cwd that is **not** the rig, and asserts
  all 3 children return `status="ok"` results. Skipped when `bd` is
  not on PATH (matches the existing `test_snakes_demo_provision.py`
  pattern). Won't run in the loop's `run_tests` step (rig has
  `PO_SKIP_E2E=1`); manual gate.

No changes needed in the `software-dev` pack repo
(`/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/`).
The pack consumes `BeadsStore` and `claim_issue`/`close_issue` only via
`build_registry` / `auto_store`, both of which become rig-aware in this
PR; pack-level `epic.py` and `graph.py` already pass `rig_path` through.

## Approach

The bug has two facets, both rooted in the same omission:

1. **`bd` shellouts in core do not carry `cwd=rig_path`.** Beads
   binaries resolve the local `.beads/` based on the process cwd. When
   a Prefect task runner inherits Python's process cwd (often the PO
   repo, not the user's rig), every `bd show`/`bd update`/`bd dep list`
   either targets the wrong database or fails. `_resolve_tmux_scope`
   was already patched (it passes `cwd=rig_path_p` at
   `role_registry.py:222`), but every other site is still cwd-naive.
2. **`bd show --json` shape drift.** Some `bd` versions emit a
   single-row JSON list; `BeadsStore.get`/`BeadsStore.all` assume a
   dict and crash with `AttributeError: 'list' object has no attribute
   'get'`. `_bd_show` and `_dot_suffix_children` already defend
   against this (`rows[0] if isinstance(rows, list) else rows`); the
   `BeadsStore` methods need the same treatment.

### Concrete edits

**`beads_meta.py`** — make the bd interface rig-aware:

- Promote `BeadsStore` from a 1-field dataclass to carry `rig_path:
  Path | None = None`. Add a private `_run(cmd, **kw)` helper that
  injects `cwd=str(self.rig_path)` when set. Reroute every
  `subprocess.run(["bd", ...])` in `BeadsStore.get`/`set`/`all`
  through it.
- Harden `BeadsStore.get` / `BeadsStore.all` against list-shape
  output: parse once, normalise `rows[0] if isinstance(rows, list)
  else rows`, then read `metadata`. (Mirrors the existing pattern in
  `_bd_show:202-207`.)
- Add `rig_path: Path | str | None = None` parameter to
  `auto_store(...)`; when supplied, pass it into the `BeadsStore`
  constructor. Existing callers that omit it stay backward-compatible.
- Add `rig_path: Path | str | None = None` parameter to:
  - `claim_issue`, `close_issue`
  - `_bd_available` (still cwd-independent — it only checks `which`)
  - `_bd_show`, `_bd_dep_list`, `_dot_suffix_children`
  - `list_subgraph`, `list_epic_children`, `collect_explicit_children`
    (these thread the path down to the helpers).
  Each shellout passes `cwd=str(rig_path)` when the value is non-None;
  when None, the call retains the legacy "inherit Python cwd"
  behaviour so non-rig callers (`po doctor`, `po list`) are
  unaffected. (Triage open question 2 — answered: opt-in cwd, default
  None.)
- The traversal API — `list_subgraph(root_id, ..., rig_path=None)`,
  `list_epic_children(epic_id, mode, rig_path=None)`,
  `collect_explicit_children(child_ids, rig_path=None)` — all forward
  `rig_path` into every nested helper call. The pack's `epic.py` and
  `graph.py` invoke these without `rig_path` today; we'll let them
  remain backward-compatible at the API level (defaults preserve
  current behaviour) and update the pack callsites to pass `rig_path`
  in a follow-up. **However**, for the in-flight 3mw fix to actually
  work end-to-end, the pack callsites must opt in. Do that here as
  part of the same change set, since both repos sit on this developer
  machine and the loop builds against editable installs:
  - `/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/po_formulas/epic.py`
    — pass `rig_path=rig_path` into `list_epic_children` and
    `collect_explicit_children`.
  - `/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/po_formulas/graph.py`
    — pass `rig_path=rig_path` into `list_subgraph`.
  These edits land in the pack repo (different git repo from the rig);
  builder must `cd` into each repo's `.git` ancestor before
  `git add`/`commit` per CLAUDE.md polyrepo guidance.

**`role_registry.py`** — make `BeadsStore` construction and
`build_registry`'s own shellouts rig-aware:

- `build_registry` already has `rig_path_p: Path` resolved at the top.
  Forward it into:
  - `auto_store(parent_bead, run_dir, rig_path=rig_path_p)` (new kwarg
    on `auto_store`).
  - `_resolve_pack_path(pack_path, issue_id, rig_path_p)` — add
    `cwd=str(rig_path_p)` to its inline `bd show` call.
  - The inline `bd update --set-metadata po.rig_path=...` block
    (`role_registry.py:281-294`) — pass `cwd=str(rig_path_p)`.
  - `claim_issue(issue_id, assignee=..., rig_path=rig_path_p)`.
  - `stamp_run_url_on_bead(issue_id, fr_id, dry_run=dry_run,
    rig_path=rig_path_p)` (extend that function's signature in
    `run_handles.py`).
- `_resolve_tmux_scope` already passes `cwd=rig_path_p`; leave alone.
- `RoleRegistry` itself does not call `bd` directly (the only `bd`
  shellouts via the registry happen through `self.store`). Adding
  `rig_path` to the store is sufficient — no new field on the
  registry beyond what already exists.

**`run_handles.py`** — accept and forward `cwd`:

- Add `rig_path: Path | str | None = None` to `stamp_run_url_on_bead`;
  pass `cwd=str(rig_path)` to its `bd update` shellout when non-None.
  No other functions in this module call `bd`.

### Why this shape

- **Opt-in cwd, default None.** Triage flagged that `_bd_available`
  and other non-rig-aware callers (`po doctor`, ad-hoc CLI use) must
  not break. Defaulting to `cwd=None` preserves the current
  "inherit-cwd" behaviour at every site; the rig-aware paths
  (`build_registry` → `BeadsStore` → traversal helpers) opt in.
- **Don't widen `RoleRegistry`'s API.** The store already encapsulates
  bd interaction; threading `rig_path` to `BeadsStore` is the
  smallest correct fix. Callers that build a registry directly (test
  `test_role_registry_cwd_routing`) without going through
  `build_registry` keep working with `FileStore`, which is
  cwd-independent.
- **List-vs-dict defense in depth.** `_bd_show` already does
  `rows[0] if isinstance(rows, list) else rows`. Replicate that in
  `BeadsStore.get` and `BeadsStore.all`. Don't refactor those to call
  `_bd_show` — `_bd_show` swallows non-zero exits (returns None),
  while `BeadsStore` historically uses `check=True` to surface bd
  failures loudly. Preserve that contract; only fix the shape coercion.

## Acceptance criteria

Verbatim from the issue description (no formal "Acceptance Criteria"
header — these are the issue's `Fix scope` and `Tests` bullets):

- [AC1] `prefect_orchestration/beads_meta.py`: thread `rig_path`
  through `_bd_show`, `_bd_dep_list`, `_bd_available` probe,
  `BeadsStore.{get,set,claim,close,list_epic_children}`; pass
  `cwd=str(rig_path)` to every `subprocess.run()`.
- [AC2] `prefect_orchestration/role_registry.py`: `BeadsStore` is
  constructed with `parent_id`; add `rig_path` param to ctor or carry
  it via the parent flow context.
- [AC3] List-vs-dict: harden `_bd_show` / `store.get` to handle both
  shapes (`rows[0]` when list).
- [AC4] Unit: pass `cwd` kwarg to a mocked subprocess; assert it
  equals `rig_path`. List-vs-dict shape coverage.
- [AC5] E2E (under `tests/e2e/`): provision a temp rig in `tmp_path`,
  dispatch an `epic_run` with `--dry-run`, assert all children return
  `ok` results and `bd close` was called against the rig's bd.

Reproducer from the issue must pass:

- [AC6] `cd ~/Desktop/Code/personal/snakes-demo && po run epic
  --epic-id snakes-demo-v3w --rig snakes-demo --rig-path $RIG
  --dry-run --discover deps` returns 3 dry-run task results,
  `status=ok` (manual gate; covered structurally by AC5's e2e test).

## Verification strategy

| AC | Concrete check |
|---|---|
| AC1 | New unit tests in `tests/test_beads_meta_cwd.py` monkeypatch `subprocess.run` and assert `kwargs["cwd"] == str(rig_path)` for every method on `BeadsStore` (`get`/`set`/`all`), `claim_issue`, `close_issue`, `_bd_show`, `_bd_dep_list`, `_dot_suffix_children`, `list_subgraph`, `list_epic_children`, `collect_explicit_children`. Each parametrised over `(rig_path=None → no cwd kwarg or cwd=None)` and `(rig_path=tmp_path → cwd=str(tmp_path))`. |
| AC2 | Extend `test_role_registry.py` with a test that monkeypatches `subprocess.run` to record `(cmd, cwd)` tuples, runs `build_registry(..., dry_run=False)` (with `shutil.which` faked to claim `bd` is present), then asserts every recorded `bd ...` call has `cwd == str(rig_path)`. Also asserts `reg.store` (the `BeadsStore` instance) carries `rig_path`. |
| AC3 | Two unit tests in `tests/test_beads_meta_cwd.py`: (a) `BeadsStore.get` returns the metadata value when `bd show --json` returns a single-element list; (b) when `bd show --json` returns a dict (legacy path), still works. Same coverage for `BeadsStore.all`. |
| AC4 | Covered by AC1+AC3 unit tests. Run via `uv run python -m pytest tests/test_beads_meta_cwd.py tests/test_beads_graph.py tests/test_role_registry.py -v`. |
| AC5 | New `tests/e2e/test_epic_dispatch_cwd.py`: in `tmp_path`, run `bd init`, `bd create --type=epic ...` for the epic, `bd create --type=task ...` ×3 for children, `bd dep add --type=parent-child <child> <epic>` ×3. Then change Python cwd to **outside** `tmp_path`, import and call `epic_run(epic_id, rig=..., rig_path=str(tmp_path), dry_run=True, discover="deps")` directly via the pack import (since `_dispatch_nodes` runs Prefect tasks). Assert returned dict has 3 entries each with `status="ok"` (or whatever the StubBackend produces). Skip when `bd` not on PATH. |
| AC6 | Documented manual reproduction step in `lessons-learned.md` after the fix lands; rerun `po run epic ...` against the snakes-demo rig from a non-rig cwd and confirm 3 dry-run task results. Not a CI check; gate before close. |

## Test plan

- **unit** (primary regression net for the loop):
  - `tests/test_beads_meta_cwd.py` *(new)* — AC1, AC3, AC4 coverage.
  - `tests/test_beads_graph.py` *(extend)* — assert cwd kwarg
    propagation through `list_subgraph` / `list_epic_children` /
    `collect_explicit_children` when `rig_path` is passed.
  - `tests/test_role_registry.py` *(extend)* — AC2 coverage:
    `build_registry` non-dry-run with mocked subprocess records every
    bd call's cwd.
  - Run: `uv run python -m pytest tests/test_beads_meta_cwd.py
    tests/test_beads_graph.py tests/test_role_registry.py
    tests/test_resume.py tests/test_run_lookup.py -v` (the trailing
    two exercise the other consumers of `_bd_show` shape coercion to
    catch regressions there).
  - Full unit suite: `uv run python -m pytest tests/ --ignore=tests/e2e`.
- **e2e** (manual gate; **not** run by the loop because rig has
  `PO_SKIP_E2E=1`):
  - `tests/e2e/test_epic_dispatch_cwd.py` *(new)* — AC5 coverage.
  - Run manually before declaring fix verified:
    `uv run python -m pytest tests/e2e/test_epic_dispatch_cwd.py -v`.
- **playwright**: N/A. No UI in this repo.

The new e2e test follows the layering rule from CLAUDE.md ("real
subprocesses → tests/e2e/"). It does *not* belong in `tests/` because
it shells out to real `bd` and (transitively) the real Prefect engine.

## Risks

- **Signature ripple beyond the bug's immediate scope.** Adding
  `rig_path` to `auto_store`, `claim_issue`, `close_issue`,
  `list_epic_children`, `collect_explicit_children`, `list_subgraph`,
  and `stamp_run_url_on_bead` is API-additive (default None). Existing
  pack and core callers continue to compile. The pack callsites in
  `epic.py` / `graph.py` will be updated in the same change set; this
  is a polyrepo edit (two `.git` ancestors). If only the core repo
  ships, the bug stays partially unfixed for `--discover deps`/`both`
  (epic discovery), because the pack still calls without `rig_path`.
  Builder must commit both repos.
- **Concurrent-rig assumption.** The Prefect runner can dispatch
  multiple flows in the same Python process (work-pool concurrency).
  Threading `rig_path` per call (rather than process-global) is the
  right choice; no risk from cross-flow cwd contamination since each
  call binds its own `cwd=`. Confirmed by reading
  `_dispatch_nodes`: each child future receives `rig_path` via kwargs.
- **`_bd_available` cache bypass.** The probe is a thin
  `shutil.which` call, not memoised. Safe to leave cwd-independent.
- **Backward compat for `BeadsStore` direct callers.** The dataclass
  now has an optional `rig_path` field; positional `BeadsStore(parent_id="x")`
  still works. `kw_only=True` not applied (would break existing
  positional construction in a few tests).
- **Cross-formula self-masking (triage open Q4).** PO running on its
  own rig has cwd == rig_path coincidentally — masks the bug in
  self-dev runs. Fix: tests that explicitly assert `cwd=rig_path` (not
  rely on coincidence). This is baked into AC1/AC2 above.
- **No migrations, no DB schema, no API contract change.** Pure
  signature-additive change to internal Python helpers. No external
  consumers (the `po` CLI doesn't expose `BeadsStore` directly).
- **Minor risk: existing `BeadsStore.get` semantics**. The current
  implementation uses `check=True` (raises on bd failure); some
  callers may depend on the exception. We keep `check=True` and only
  add cwd + shape coercion, preserving the contract.
- **Stale assignee on retry**. Bead is `in_progress` and assigned to
  `po-110ca201` (this run). No live worker collision, so we don't need
  to reset assignment.
