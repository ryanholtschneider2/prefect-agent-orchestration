# Implementation Summary: prefect-orchestration-7vs.2

## Issue
Persist Claude `--resume` UUIDs **per role on a seed bead** (topmost
parent-child ancestor), so successive child beads of the same parent
inherit the role's session and Claude resumes prior conversation across
`build.iter1 → build.iter2 → build.iter3`. Migrate transparently from
today's per-bead `metadata.json` storage; preserve fork semantics for
critic-on-new-branch.

## What Was Implemented

### Files Modified
| File | Changes |
|------|---------|
| `prefect_orchestration/beads_meta.py` | Added `resolve_seed_bead(issue_id, rig_path, *, max_hops=16)`. Iterative walk over `_bd_dep_list(direction="down", edge_type="parent-child")` (verified direction against this rig 2026-04-28). Cycle guard, no-bd → returns `issue_id` unchanged. |
| `prefect_orchestration/role_registry.py` | Added `role_session_store: RoleSessionStore \| None` field on `RoleRegistry`; rewired `get`/`persist`/`_refresh_handles` through new `_read_session`/`_write_session` indirections (fall back to `store.get/set("session_<role>")` when no `role_session_store` is wired, preserving back-compat for direct constructors). Added `persist_to(role, seed_id)` for branch-fork case. `build_registry` now resolves `seed_id` via `parent_bead`-then-`resolve_seed_bead`-then-`issue_id`, constructs a `RoleSessionStore`, and passes it on. Legacy `store` field retained. |
| `prefect_orchestration/sessions.py` | Added `load_role_sessions(run_dir, *, seed_id, seed_run_dir, rig_path)` — returns the prefixed-key shape (`{"session_<role>": uuid}`) so `build_rows`/`lookup_session` work unchanged. `load_metadata` retained as thin back-compat wrapper. |
| `prefect_orchestration/cli.py` | `po sessions`: resolves `seed_id` via `resolve_seed_bead`, derives `seed_run_dir` from the formula dir, calls `load_role_sessions`. Preserves the legacy "exit 3 / no metadata.json" error code only when *all* tiers are empty AND no legacy file exists. Updated `--keep-sessions` help text. |
| `prefect_orchestration/retry.py` | Docstring update only — clarifies `--keep-sessions` is a no-op for seed-bead-resident sessions; legacy shim in `RoleSessionStore` resurfaces archived `metadata.json` automatically. |

### Files Created
| File | Purpose |
|------|---------|
| `prefect_orchestration/role_sessions.py` | `RoleSessionStore` dataclass: seed-bead-keyed role→uuid persistence with three-tier read order (`BeadsStore(seed_id)` → `<seed_run_dir>/role-sessions.json` → legacy `<self_run_dir>/metadata.json`) and atomic JSON write (`tempfile.mkstemp` + `os.replace`). Internal API is bare-role keys; `sessions.load_role_sessions` adds the `session_` prefix at the boundary. |
| `tests/test_beads_meta.py` | 8 tests covering `resolve_seed_bead`: chain walk, self-seed, no-bd fallback, cycle guard, max-hops cap, multi-parent determinism, direction/edge-type assertions, rig_path threading. |
| `tests/test_role_sessions.py` | 13 tests covering tier ordering (legacy < json < beads), set→beads-or-json fallback, "set after legacy hit doesn't mutate legacy", `all()` unioning, atomic-write tempfile cleanup, corrupt-file tolerance. |

### Files Touched (Tests Added)
| File | Tests Added |
|------|-------------|
| `tests/test_role_registry.py` | `test_seed_inheritance_across_children` (AC a), `test_persist_to_new_seed_does_not_pollute_original` (AC b). |
| `tests/test_sessions.py` | `test_load_role_sessions_unioned`, `test_load_role_sessions_returns_empty_when_all_tiers_empty`. |

### Key Implementation Details

- **Direction verified** against this live rig before coding: `bd dep list <child> --direction=down --type=parent-child --json` returns the **parent**. Encoded as a comment in `resolve_seed_bead`'s docstring with the verification date.
- **Tier precedence**: BeadsStore wins over role-sessions.json wins over legacy `metadata.json`. `set()` writes to BeadsStore when the seed bead exists, otherwise to the JSON file. Legacy file is read-only (preserves archived forensic state — AC c follow-up).
- **`load_role_sessions` returns prefixed keys** (`{"session_builder": uuid, ...}`) so existing `build_rows`/`lookup_session`/render helpers consume it without modification (per plan amendment 2).
- **Atomic writes**: `tempfile.mkstemp(prefix=".role-sessions.", suffix=".json.tmp", dir=seed_run_dir)` + `os.fsync` + `os.replace`. Ensures crash-safety on the JSON-file fallback path.
- **No `_last_was_fork` instrumentation** (per plan amendment 4 — out of scope).
- **`AgentSession` not modified** (per design decision 6).

## Public API Additions
- `prefect_orchestration.beads_meta.resolve_seed_bead(issue_id, rig_path=None, *, max_hops=16) -> str`
- `prefect_orchestration.role_sessions.RoleSessionStore` dataclass
- `prefect_orchestration.sessions.load_role_sessions(run_dir, *, seed_id, seed_run_dir, rig_path=None) -> dict[str, str]`
- `RoleRegistry.persist_to(role: str, seed_id: str) -> None`
- New optional field `RoleRegistry.role_session_store: RoleSessionStore | None`

## Migration Behavior

Three legacy data shapes resolve transparently with zero data migration:

1. **Solo-run `<run_dir>/metadata.json`** with `session_<role>` keys —
   read via the migration shim in `RoleSessionStore._read_legacy`. Subsequent
   `set()` calls promote to BeadsStore (or `role-sessions.json`); the
   legacy file is never mutated, so an archived run-dir keeps its
   forensic snapshot.
2. **Epic/graph `bd show <epic>.metadata.session_<role>`** — already in
   tier 1 (`BeadsStore` read). No translation needed.
3. **Mixed metadata.json** with both session + non-session keys — sessions
   read via shim; non-session keys keep flowing through the legacy
   `RoleRegistry.store` (kept as a no-cost optional seam per plan Q3).

## Acceptance Criteria Status

| Criterion | Status | Notes |
|-----------|--------|-------|
| (a) Parent → child1 → child2 reuses `--resume <uuid>` | DONE | `tests/test_role_registry.py::test_seed_inheritance_across_children` proves two `RoleRegistry`s with the same seed share the role's uuid. Real-world e2e gated on `.po-env PO_SKIP_E2E=1` per plan; manual verification listed below. |
| (b) Forking preserved on critic-on-new-branch | DONE | `RoleRegistry.persist_to(role, seed_id)`; `tests/test_role_registry.py::test_persist_to_new_seed_does_not_pollute_original` asserts the original seed's map is untouched. |
| (c) Migration shim — legacy per-bead `metadata.json` readable | DONE | `tests/test_role_sessions.py::test_get_reads_legacy_when_only_legacy_present` + `::test_set_after_legacy_hit_does_not_mutate_legacy_file`. |
| (d) `sessions.py` + `agent_session.py` + `RoleRegistry` wired through | DONE | `RoleRegistry` reads/writes via `RoleSessionStore`; `sessions.load_role_sessions` unions all tiers; `cli.py` `po sessions` rewired. `agent_session.py` is unmodified — it never touched storage directly (per plan design decision 6). |

## How to Demo

Manual e2e (matches plan §"Verification Strategy" — gated on `.po-env`):

1. `po run epic --epic-id <small-2-child-epic> --rig <rig> --rig-path <path>`
2. Inspect: `bd show <epic-id>` should now show `metadata.session_builder = <uuid>` (BeadsStore tier wrote on seed).
3. `po sessions <child1>` and `po sessions <child2>` should display the **same** builder uuid (seed-shared).
4. The second child's claude subprocess invocation includes `--resume <uuid>` (visible in tmux scrollback or via `po sessions <child2> --resume builder`).

Unit-test demo:
```bash
uv run python -m pytest --tb=short -q --ignore=tests/e2e \
  tests/test_beads_meta.py tests/test_role_sessions.py \
  tests/test_role_registry.py tests/test_sessions.py
# 44 passed
```

## Test Run Results

```
uv run python -m pytest --tb=short -q --ignore=tests/e2e
# 10 failed, 703 passed, 2 skipped
```

The 10 failures match the pre-existing baseline noted in the task brief
(`test_cli_packs.py::*`, `test_agent_session_tmux.py::test_session_name_derivation`,
`test_deployments.py::test_po_list_still_works`,
`test_mail.py::test_prompt_fragment_exists_and_mentions_inbox`). All
unrelated to this issue.

## Deviations from Plan

- **`load_metadata` not deprecated** — kept as a back-compat thin
  wrapper (rather than marking it deprecated) because nothing in this
  scope requires its removal and a deprecation warning would surface
  noise in unrelated callers. Plan §File-by-file used "deprecated thin
  wrapper" language; the actual implementation is just "thin wrapper",
  matching plan §Migration shim ("kept as thin wrapper for back-compat").
- **`po sessions` exit-code 3 fallback** — preserved the legacy "no
  metadata.json → exit 3" path for the case where the tier union is
  empty AND no legacy file exists. Plan didn't enumerate this edge
  case explicitly; chose to keep the existing scripted-caller contract
  intact (`tests/test_sessions.py::test_sessions_missing_metadata_json`
  still passes unchanged).
- **`RoleRegistry` legacy fallback** — when `role_session_store=None`
  (direct construction without `build_registry`), `_read_session`/
  `_write_session` fall back to `store.get/set("session_<role>")`.
  This is not strictly required by the plan but protects existing
  test fixtures (`test_role_registry.py::test_role_registry_cwd_routing`
  constructs `RoleRegistry` directly with only a `FileStore`) from
  breaking. No production caller exercises this path; `build_registry`
  always wires the store.

## Known Issues or Limitations

- **Concurrent BeadsStore writes** are last-writer-wins via dolt-server
  serialization (this rig's default). Documented in the plan; both
  uuids are valid resumes so this is fine.
- **JSON-file fallback under concurrent writers** can lose updates if
  two processes race on `set()` (no file lock). Acceptable for the
  no-bd dev-only path per plan §Risks.
- **`po sessions` semantics shift**: now displays seed-shared sessions
  for child beads, not just per-bead. Documented in the `--keep-sessions`
  help text update; identical behavior for solo runs (seed = self).

## Notes for Review

- The reviewer should sanity-check the `po sessions` exit-code logic
  in `cli.py` (`if not metadata: ... if not legacy_path.exists(): exit 3`).
  The semantics: a fresh seed-only run with no legacy + no role-sessions.json
  yet is treated as "no sessions yet" → exit 3 with the same message,
  preserving the existing `test_sessions_missing_metadata_json` contract.
- `RoleRegistry.persist_to` constructs a fresh `RoleSessionStore` per
  call (one-shot). Cheap and stateless; avoids needing to track a
  branch-store in the registry. The branch_seed's `seed_run_dir` is
  derived from `self.role_session_store.seed_run_dir.parent` — assumes
  same formula. Documented in the docstring; cross-formula branching
  would need an explicit override.
- The legacy fallback in `RoleRegistry._read_session`/`_write_session`
  (when `role_session_store is None`) was added defensively for
  test-fixture compatibility. It is **not** the production path; all
  `build_registry`-constructed registries get a real `RoleSessionStore`.
