# Plan — prefect-orchestration-u1x

Lift `RoleRegistry` from the `po-formulas` pack into core
(`prefect_orchestration.role_registry`) and add a `build_registry(...)`
factory that bundles the ~80-line bootstrap currently inlined at the top
of `software_dev_full`. Pack imports from core; no behavior change.

## Affected files

**Core (`/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration`)**:
- **NEW** `prefect_orchestration/role_registry.py` — `RoleRegistry`
  dataclass + `build_registry(...)` factory.
- `tests/test_role_registry.py` — **NEW** unit test for build_registry
  with `dry_run=True` (StubBackend, no bd shellouts).

**Pack (`/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas`)** — separate repo, edit + commit there:
- `po_formulas/software_dev.py` — delete the local `RoleRegistry` class
  (lines ~99–205) and the inline bootstrap block (lines ~536–681);
  replace with `from prefect_orchestration.role_registry import RoleRegistry, build_registry`
  and one call `reg, base_ctx = build_registry(issue_id, rig, rig_path, _AGENTS_DIR, pack_path=pack_path, parent_bead=parent_bead, dry_run=dry_run, claim=claim, roles=SOFTWARE_DEV_ROLES)`.
  `SOFTWARE_DEV_ROLES` constant stays in the pack and is passed in.

## Approach

### Core module `prefect_orchestration/role_registry.py`

1. **Move `RoleRegistry`** (`software_dev.py:102–205`) verbatim, with one
   small generalization to break the SDF-specific dependency: replace the
   module-level `SOFTWARE_DEV_ROLES` reference inside `_refresh_handles`
   with a new `roles: tuple[str, ...]` field on the dataclass (default
   `()`, set by `build_registry`). Likewise `_CODE_ROLES` becomes a
   `code_roles: frozenset[str]` field with default
   `frozenset({"builder", "linter", "cleaner"})` — same default behavior,
   per-pack overridable. No other shape changes.

2. **`build_registry(issue_id, rig, rig_path, agents_dir, *, pack_path=None, parent_bead=None, dry_run=False, claim=True, roles=(), code_roles=None) -> tuple[RoleRegistry, dict[str, Any]]`**
   bundles, in this order (mirrors `software_dev_full` lines ~536–681):
   - Resolve `rig_path_p`, create `run_dir = rig_path_p/.planning/<formula>/<issue>`
     and `verdicts/` subdir. Formula name is derived from `agents_dir.parent.name`
     fallback `"software-dev-full"` — keep the on-disk path identical for SDF.
     **Decision:** to preserve the exact existing path, accept an optional
     `formula_name: str = "software-dev-full"` kwarg; SDF passes the
     default, future packs override.
   - `pack_path_p = _resolve_pack_path(pack_path, issue_id, rig_path_p)`
     — move `_resolve_pack_path` from the pack to core (same module).
   - Stamp `po.rig_path` / `po.run_dir` / `po.pack_path` via `bd update --set-metadata`
     (best-effort; skipped on dry_run or no `bd`).
   - `store = auto_store(parent_bead, run_dir)`
   - Backend-factory selection: keep the existing inline `PO_BACKEND`
     switch verbatim (`cli` / `stub` / `tmux-stream` / `tmux` / auto).
     Triage's note about `backend_select.choose_backend()` is aspirational;
     for "no behavior change" we move the inline block as-is. Follow-up
     refactor to consolidate with `backend_select.select_default_backend`
     is out of scope.
   - `fr_id = flow_run.get_id() or "local"`
   - Compute `tmux_scope` (parent-bead lookup via `bd show --json`,
     fallback `rig`).
   - Construct `RoleRegistry(...)` with `roles=roles`, `code_roles=code_roles or frozenset({"builder","linter","cleaner"})`.
   - Tag flow run with `issue_id:<id>` (best-effort Prefect client call).
   - `reg._refresh_handles()` — seed `write_run_handles`.
   - `stamp_run_url_on_bead(issue_id, fr_id, dry_run=dry_run)`.
   - `if claim and not dry_run: claim_issue(issue_id, assignee=f"po-{fr_id[:8]}")`.
   - Build `base_ctx = {"issue_id", "rig", "rig_path", "pack_path", "run_dir"}`.
   - Return `(reg, base_ctx)`.

3. **Imports inside core**: pulls in `agent_session`, `beads_meta`,
   `run_handles`, `prefect.runtime.flow_run`, `prefect.client.orchestration`.
   None of those import `po_formulas.*` (verified by inspection of
   `prefect_orchestration/` — no cross-pack imports). Safe; no cycle.

### Pack edits

- Drop `_CODE_ROLES`, `RoleRegistry`, `_resolve_pack_path` from
  `software_dev.py`.
- Replace lines ~536–681 of `software_dev_full` with one call:
  `reg, base_ctx = build_registry(issue_id, rig, rig_path, _AGENTS_DIR, pack_path=pack_path, parent_bead=parent_bead, dry_run=dry_run, claim=claim, roles=SOFTWARE_DEV_ROLES)`.
- Keep `SOFTWARE_DEV_ROLES` defined in `software_dev.py` (it's the
  per-formula role list — packs own their own). Pass into
  `build_registry`.
- Optional shim: `RoleRegistry = RoleRegistry  # re-export` at top of
  `software_dev.py` so any in-flight branch importing
  `po_formulas.software_dev.RoleRegistry` keeps working for one cycle.
  Triage flagged this as nice-to-have; include it.

### Install + smoke

After the pack edit: `po packs update` to refresh entry-point metadata
(no EP groups change, but uv reinstall picks up the new pack version).

## Acceptance criteria (verbatim)

1. `prefect_orchestration.role_registry` module exists with `RoleRegistry` + `build_registry`
2. `software_dev.py` imports from core, no local `RoleRegistry`
3. `software-dev-full` runs end-to-end against a smoke bead and closes it

## Verification strategy

- **AC1**: `python -c "from prefect_orchestration.role_registry import RoleRegistry, build_registry; print(RoleRegistry, build_registry)"` succeeds.
- **AC2**: `grep -n "^class RoleRegistry" /home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/po_formulas/software_dev.py` returns no matches; `grep -n "from prefect_orchestration.role_registry import" software_dev.py` returns one match.
- **AC3**: Run `po run software-dev-full --issue-id <smoke-bead> --rig prefect-orchestration --rig-path /home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration --dry-run` first (StubBackend, fast) — confirms the factory wiring without a real Claude run. Then a real end-to-end run on a small bead and verify `bd show <id>` shows `closed`. Decision-log + lessons-learned land in run_dir.

## Test plan

- **unit** (`tests/test_role_registry.py`): construct a `build_registry` call
  with `dry_run=True` against a tmp rig (`bd init --embedded` in fixture
  or skip if `bd` missing), assert it returns `(RoleRegistry, dict)` with
  the expected `base_ctx` keys and that `run_dir/verdicts` exists.
  Mock `flow_run.get_id` to return a stable id.
- **e2e**: existing `tests/e2e/` tests already exercise `po run` CLI
  roundtrips; the dry-run smoke covers AC3 lite. Full real run is the
  human gate.
- **playwright**: N/A (no UI).

## Risks

- **Cross-repo edits**: pack lives at sibling
  `/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/`,
  not under the rig. Builder must `cd` into that repo for `git add` /
  `commit`, and run `po packs update` after install. Bead's
  `po.pack_path` metadata points at the rig itself — ignore it; use the
  sibling path.
- **Behavior parity**: the inline bootstrap is finicky (PO_BACKEND
  switch, parent-bead lookup, flow-run tagging). A near-mechanical move
  is required; resist refactoring. Diff old vs new line-by-line during
  the build iter.
- **Import cycle**: confirmed no `po_formulas` import in any core
  module currently — the move is safe. Re-verify after edit.
- **Backwards compat**: re-export `RoleRegistry` from
  `po_formulas.software_dev` for one cycle to avoid breaking any open
  branch.
- **No migration / API contract change**: pure internal refactor; no
  on-disk format, deployment, or CLI surface change.
