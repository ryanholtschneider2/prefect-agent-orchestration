# Decision Log — prefect-orchestration-7vs.2

## bd dep direction verified
Ran the four mandated probes against this rig:
- `bd dep list <child:7vs.2> --direction=down --type=parent-child --json` → returns the **parent** (7vs).
- `bd dep list <parent:7vs> --direction=up --type=parent-child --json` → returns the **children** (7vs.1..7vs.7).
- `bd dep list <child:7vs.2> --direction=up --type=parent-child --json` → `[]`.
- `bd dep list <parent:7vs> --direction=down --type=parent-child --json` → `[]`.

**Decision:** `resolve_seed_bead` walks via `_bd_dep_list(cur, direction="down", edge_type="parent-child")` to discover ancestors; matches plan §"Seed-bead resolution".

## RoleSessionStore lives in its own module
**Why:** Plan §File-by-file. Keeps `beads_meta.py` from growing further; isolates the three-tier read order + atomic JSON write.

## `legacy_self_run_dir` keyword
**Why:** Migration shim must read the *bead's own* legacy `metadata.json` (where solo `auto_store(parent_id=None)` runs wrote `session_<role>` keys), distinct from the seed-bead's run-dir (where the new `role-sessions.json` lives). Two paths, two parameters.

## set() does not mutate the legacy file
**Why:** AC (c) — migration shim is read-only. New writes go to BeadsStore (preferred) or new `role-sessions.json`. Legacy file is left alone so an archived run_dir keeps its forensic snapshot intact.

## `load_role_sessions` returns *prefixed* keys
**Why:** Plan amendment 2. `sessions.build_rows` and `lookup_session` already strip the `session_` prefix. Returning the prefixed shape means zero changes to those helpers. Internal `RoleSessionStore` API uses bare-role keys; conversion at the sessions.py boundary.

## `seed_id` resolution precedence in build_registry
**Why:** Plan §"build_registry integration". Caller-supplied `parent_bead` wins (preserves today's epic/graph BeadsStore semantics). Else if bd is on PATH and not dry_run, walk parent-child edges. Else fall back to `issue_id` (self-seed = today's solo-run behavior, identical).

## Keep legacy `store` field on RoleRegistry
**Why:** Plan Q3 + design decision: `_refresh_handles` and `links.md` may want to read other keys later. Removing the seam costs nothing to keep and avoids a future refactor.
