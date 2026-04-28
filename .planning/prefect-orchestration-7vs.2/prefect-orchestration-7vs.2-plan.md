# Implementation Plan: prefect-orchestration-7vs.2

## Issue Summary

Persist Claude `--resume` UUIDs **per role on a seed bead** (parent / topmost
ancestor in same formula), so successive child beads of the same parent
inherit the role's session UUID and Claude resumes its prior conversation
across `build.iter1 → build.iter2 → build.iter3`. Migrate transparently from
today's per-bead `metadata.json` storage. Forking semantics for critic-on-new-
branch must still produce a fresh UUID.

---

## Research Summary

### Where role→uuid lives today

| Concern | Code | Notes |
|---|---|---|
| Read on session construction | `role_registry.py:82` `sid = self.store.get(f"session_{role}")` | One spot only. |
| Write after each turn | `role_registry.py:91-94` `RoleRegistry.persist` → `store.set(f"session_{role}", sess.session_id)` | Pack calls `reg.persist(role)` after every `reg.get(role).prompt(...)` (e.g. `software_dev.py:110, 124, 133, 147`). |
| `links.md` refresh | `role_registry.py:128-145` `_refresh_handles` | Reads `session_<role>` keys per role from same `store`. |
| Store backend selection | `beads_meta.py:108-121` `auto_store(parent_id, run_dir)` | If `parent_id` is given **and** `bd` on PATH → `BeadsStore` writing onto that bead's metadata. Else → `FileStore` at `<run_dir>/metadata.json`. |
| `BeadsStore` | `beads_meta.py:35-78` | `bd show <parent_id> --json` → `metadata`; `bd update --set-metadata key=val`. Writes are scoped to `parent_id`. |
| `po sessions` CLI | `cli.py:723-738` calls `sessions.load_metadata(loc.run_dir)` | Reads ONLY the per-run `metadata.json` file. Not `BeadsStore` aware. |
| Run-dir layout | `<rig_path>/.planning/<formula>/<issue_id>/metadata.json` | Set in `build_registry`, `role_registry.py:273`. |

**Key existing-but-incomplete behavior**: when callers pass `parent_bead`
explicitly (`epic.py:139` passes `epic_id`; `graph.py:268` passes `root_id`;
`software_dev.py:708` plumbs an optional `parent_bead` kwarg through
`epic_run`/`graph_run`), `BeadsStore` already accumulates `session_<role>`
metadata on the parent bead → child runs of that epic already share role
sessions. The gap is two-fold:

1. **Solo `po run software-dev-full --issue-id <child>`** invocations don't
   pass `parent_bead`, so `auto_store` falls back to `FileStore` per-run
   `metadata.json`. No inheritance. No discovery of an existing
   `session_<role>` written by a sibling under the same parent.
2. **`po sessions <id>`** reads only `<run_dir>/metadata.json`; for a
   `BeadsStore`-backed run that file is empty of session keys (writes go to
   the parent bead). The CLI doesn't surface what's actually persisted.

### Engdocs ground truth

- `principles.md §5` (compose before invent): role-session affinity is a
  composition over existing primitives (`BeadsStore`, `bd dep`) — no new
  Protocol, no new entry-point group. Just plumbing.
- `principles.md §"Prompt authoring"` / `primitives.md` row 26: per-role
  session resume is named as a covered primitive — this issue tightens it
  for graph fan-out.
- No decision-record contradicts this design.

### External libraries

None. All behavior is `bd` shellouts + JSON files we already maintain.

---

## Proposed Architecture

### Seed-bead resolution

For an issue `I`, the **seed bead** is the topmost ancestor reachable via
`bd dep list <I> --direction=down --type=parent-child` (i.e. walk *up* the
parent chain) that itself still has a parent-child parent. If `I` has no
parent-child parent, **`I` itself is the seed**. This makes:

- Solo run on a parentless bead → seed = self → identical to today's
  per-bead `metadata.json` behavior.
- `epic` / `graph` run dispatching child `C` of parent `P` → seed = `P`
  (or higher if `P` itself has a parent).
- `build.iter1`, `build.iter2`, `build.iter3` all under parent `B` → seed =
  `B`, all three inherit and update the same `session_<role>` map.

Resolution lives in a new helper `beads_meta.resolve_seed_bead(issue_id, rig_path)`:

```python
def resolve_seed_bead(issue_id: str, rig_path: Path | str | None = None) -> str:
    """Walk parent-child edges upward; return topmost ancestor (or issue itself).

    No bd → returns issue_id (FileStore path takes over downstream).
    Cycle guard: cap at 16 hops; raise ValueError on cycle.
    """
```

Implemented via `_bd_dep_list(cur, direction="down", edge_type="parent-child")`
(direction="down" returns *what `cur` depends on*, where parent-child
edges point from child → parent in bd's model — verify with a one-shot
`bd dep list <known-child> --direction=down --type=parent-child --json`
during build). Iterative loop, no recursion.

### New on-disk shape — role-sessions file

A new file lives alongside the seed bead's run-dir:

```
<rig_path>/.planning/<formula>/<seed_id>/role-sessions.json
```

```json
{
  "version": 1,
  "sessions": {
    "builder":  "abc-123-...",
    "critic":   "def-456-...",
    "verifier": "ghi-789-..."
  }
}
```

**Rationale for a separate file (not reusing `<run_dir>/metadata.json`):**

- `metadata.json` is per-run forensic state (verdicts pointer, run_id, etc.).
  Mixing shared cross-run keys into it confuses `po artifacts` /
  `po retry` semantics.
- File lives under the *seed* bead's run-dir (which exists if the seed has
  ever been the entry-point of a `po run`; if not — e.g. seed is an epic
  bead never directly run — the file is created on first child write).
  Auto-create the seed run-dir if missing on first write.
- Plain JSON, atomic write via temp+rename (already the FileStore pattern).
- Layout deliberately mirrors existing `session_<role>` metadata under
  `BeadsStore` so the migration shim can union them.

### `MetadataStore` extension

Add a new dedicated abstraction layered over the existing `MetadataStore`:

```python
# prefect_orchestration/role_sessions.py  (new module)
@dataclass
class RoleSessionStore:
    """Read/write the role→uuid map keyed by a seed bead.

    Tries BeadsStore on the seed (writes 'session_<role>' metadata onto the
    seed bead; reflects across all children of that seed automatically). Falls
    back to a JSON file at <seed_run_dir>/role-sessions.json when bd is not
    available or no seed bead exists (singleton run).
    """
    seed_id: str
    seed_run_dir: Path  # <rig_path>/.planning/<formula>/<seed_id>/
    rig_path: Path | None = None

    def get(self, role: str) -> str | None: ...
    def set(self, role: str, uuid: str) -> None: ...
    def all(self) -> dict[str, str]: ...
```

`get` lookup order (read-side):

1. `BeadsStore(seed_id)` — `session_<role>` metadata key (matches today's
   on-parent encoding for epic runs; **no migration needed** for those).
2. `<seed_run_dir>/role-sessions.json` `sessions[role]`.
3. **Migration shim:** `<self_run_dir>/metadata.json` `session_<role>`
   (legacy per-bead `FileStore` writes). Caller passes `self_run_dir`
   to `RoleSessionStore` for shim lookup; on read-hit, the value is
   *also* persisted forward to (1)/(2) on next `set`.

`set` write-order:

1. If bd available **and** seed bead exists → write to `BeadsStore(seed_id)`
   (single source of truth; visible via `bd show`).
2. Else → write to `<seed_run_dir>/role-sessions.json`.

(One backend at a time; not dual-writes — eliminates split-brain.)

### Forking semantics (AC b)

Today: `AgentSession.prompt(fork=True)` calls into the backend with
`fork=True`; the backend allocates a *new* session UUID via
`--session-id <new> --resume <prior> --fork-session`, which the orchestrator
captures in `self.session_id` post-turn. **No change to `AgentSession`.**

The "fork creates a fresh role on a new branch" case is unambiguous:
`reg.persist(role)` after a forked turn writes the *new* UUID to the
seed's role-sessions map, replacing the prior. That overwrites the
inheritance chain — which is wrong for the original branch but correct
for forked descendants. The fix is **scope at the registry level, not the
session level**:

- `RoleRegistry` gets a new optional field `fork_scope: str | None = None`.
  When a critic explicitly intends to fork a role onto a new sub-graph
  branch, the formula creates a child `RoleRegistry` whose seed_id is
  the *branch root* (the bead the critic is forking work onto), not the
  parent epic. The fresh fork UUID is written there.
- For the common case (90%+) — every iteration of a single role inside one
  child bead — there is no fork ambiguity. The `fork=True` flag on
  `AgentSession.prompt` already reflects "this turn forks"; reg.persist
  writes the post-turn UUID to whatever seed the registry was constructed
  with. If the formula author wants the fork's UUID to NOT pollute the
  inheritance chain, they call `reg.persist_to(role, seed_id=branch_id)`
  (new method) instead of `reg.persist(role)` for that specific turn.

`persist_to(role, seed_id)` instantiates a one-shot `RoleSessionStore`
pointing at `seed_id`'s run-dir and writes there. Default `persist`
keeps writing to the registry's bound seed. This makes the fork-vs-resume
distinction **explicit at the formula call site** — the agent's behavior
is unchanged.

### `build_registry` integration

`role_registry.build_registry` becomes seed-aware:

```python
def build_registry(
    issue_id: str, rig: str, rig_path: str, agents_dir: Path,
    *,
    pack_path: str | None = None,
    parent_bead: str | None = None,   # explicit override (epic/graph callers)
    dry_run: bool = False,
    ...
) -> tuple[RoleRegistry, dict[str, Any]]:
    ...
    # NEW: resolve seed bead
    if parent_bead is not None:
        seed_id = parent_bead  # explicit caller wins; matches today's BeadsStore behavior
    elif not dry_run and shutil.which("bd"):
        seed_id = resolve_seed_bead(issue_id, rig_path=rig_path_p)
    else:
        seed_id = issue_id
    seed_run_dir = rig_path_p / ".planning" / formula_name / seed_id
    seed_run_dir.mkdir(parents=True, exist_ok=True)

    role_session_store = RoleSessionStore(
        seed_id=seed_id, seed_run_dir=seed_run_dir, rig_path=rig_path_p,
        legacy_self_run_dir=run_dir,  # for migration shim
    )
    ...
    reg = RoleRegistry(
        ...,
        role_session_store=role_session_store,  # NEW
        store=store,  # legacy-keep; non-session metadata still flows here
    )
```

`RoleRegistry.get(role)` switches from `self.store.get(f"session_{role}")` to
`self.role_session_store.get(role)`. `RoleRegistry.persist(role)` switches
to `self.role_session_store.set(role, sess.session_id)`. The legacy
`self.store` (the `MetadataStore`) is retained for non-session metadata
that other callers might write through `BeadsStore` (today, only session
keys flow through it — but keep the seam for cleanliness).

### `po sessions <id>` integration (AC d)

`sessions.py:load_metadata` is renamed/extended:

- New `load_role_sessions(run_dir, *, seed_id, seed_run_dir, rig_path) -> dict[str, str]`
  union of: `BeadsStore(seed_id).all()` filtered to `session_*` keys +
  `seed_run_dir/role-sessions.json` + legacy `run_dir/metadata.json`
  filtered to `session_*` (migration shim). Last-writer-wins is
  `BeadsStore > role-sessions.json > legacy metadata.json`.
- Old `load_metadata` kept as thin wrapper for back-compat (nothing else
  outside the CLI calls it; tests in `tests/test_sessions.py` will be
  updated to feed the unioned dict).
- `cli.py:720-740` (`po sessions`) resolves `seed_id` via
  `resolve_seed_bead(issue_id, rig_path=...)` before calling
  `load_role_sessions`. The `ROLE | UUID | LAST-ITER | LAST-UPDATED`
  table renders unchanged; `LAST-ITER`/`LAST-UPDATED` continue to scan
  artifacts in the issue's *own* run-dir (not the seed's), since iter
  artifacts are per-bead even when the session is shared.

### Migration shim (AC c)

Three legacy data shapes must continue to resolve transparently:

| Legacy shape | Where written | Shim handling |
|---|---|---|
| `<rig>/.planning/<formula>/<id>/metadata.json` `{"session_<role>": uuid}` (FileStore, solo run) | `auto_store(parent_id=None)` path | `RoleSessionStore.get` falls through to legacy file when neither BeadsStore nor new file has the key. On hit, the value bubbles up; next `set` writes forward to BeadsStore (or new file). |
| `bd show <epic>` `metadata.session_<role> = uuid` (BeadsStore, epic/graph runs today) | `auto_store(parent_id=<epic>)` | Already in primary lookup tier (1). No translation needed. |
| `<rig>/.planning/<formula>/<id>/metadata.json` *both* session + non-session keys | both paths | Sessions read via shim; non-session keys keep flowing through the legacy `store` field of `RoleRegistry`. |

No data migration script. Legacy reads are first-class for one release;
the next major can drop the shim with a deprecation warning.

---

## File-by-file change list

| File | Change |
|---|---|
| `prefect_orchestration/beads_meta.py` | Add `resolve_seed_bead(issue_id, rig_path)` — iterative `bd dep list --direction=down --type=parent-child` walk. Cycle cap at 16. Returns `issue_id` when bd missing or no parent. |
| `prefect_orchestration/role_sessions.py` (NEW) | `RoleSessionStore` dataclass with `get`/`set`/`all`. Three-tier lookup (BeadsStore → JSON file → legacy metadata.json shim). Atomic JSON write (tempfile + rename). |
| `prefect_orchestration/role_registry.py` | `RoleRegistry`: add `role_session_store: RoleSessionStore` field; rewire `get`/`persist`/`_refresh_handles` to use it instead of `store.get/set("session_<role>")`. Add `persist_to(role, seed_id)` for branch-fork case. `build_registry`: compute `seed_id` (use `parent_bead` if given, else `resolve_seed_bead`, else `issue_id`); construct `RoleSessionStore`; pass to `RoleRegistry`. |
| `prefect_orchestration/sessions.py` | New `load_role_sessions(run_dir, seed_id, seed_run_dir, rig_path)` returning unioned `{session_<role>: uuid}` dict. Keep `load_metadata` as deprecated thin wrapper. `lookup_session` unchanged (operates on the dict). |
| `prefect_orchestration/cli.py` (lines 720-740) | `po sessions`: import `beads_meta.resolve_seed_bead`; compute `seed_id` from `loc.rig_path` + `issue_id`; call `load_role_sessions` with both run-dirs. |
| `tests/test_role_sessions.py` (NEW) | Unit tests: tier ordering, migration shim hit, atomic write, cycle guard in `resolve_seed_bead`, no-bd FileStore path. |
| `prefect_orchestration/retry.py` + `cli.py` retry help | Update `--keep-sessions` help text: "no-op for sessions stored on the seed bead (BeadsStore) or `role-sessions.json`; only relevant for legacy `metadata.json`-resident sessions, which the migration shim now reads post-archive." No behavior change. |
| `tests/test_role_registry.py` | Add: parent-child chain inheritance (mock bd with two beads `P` and `P.child`; first run on `P.child` writes UUID to `P`; second run on different `P.child2` reads same UUID for same role). Forking via `persist_to` writes to a different seed. |
| `tests/test_sessions.py` | Update `load_metadata` callers to also exercise `load_role_sessions` with seed≠self. |
| `engdocs/principles.md` | Optional: append a one-paragraph note under "session resume" referencing the seed-bead model. (Not required if reviewer thinks principles doc is for design philosophy only.) |

---

## Skeleton code

```python
# beads_meta.py
def resolve_seed_bead(
    issue_id: str,
    rig_path: Path | str | None = None,
    *,
    max_hops: int = 16,
) -> str:
    """Topmost ancestor via parent-child edges; falls back to issue_id."""
    if not _bd_available():
        return issue_id
    cur = issue_id
    seen = {cur}
    for _ in range(max_hops):
        parents = _bd_dep_list(
            cur, direction="down", edge_type="parent-child", rig_path=rig_path
        )
        # parents is a list of {id, status, ...}; parent-child down-edges
        # from a child point at exactly one parent, but tolerate >1 by picking
        # the first deterministically (sorted by id).
        if not parents:
            return cur
        nxt = sorted(p["id"] for p in parents if p.get("id"))[0]
        if nxt in seen:
            raise ValueError(f"parent-child cycle through {cur}->{nxt}")
        seen.add(nxt)
        cur = nxt
    raise ValueError(f"parent-child chain exceeds {max_hops} hops from {issue_id}")


# role_sessions.py
@dataclass
class RoleSessionStore:
    seed_id: str
    seed_run_dir: Path
    rig_path: Path | None = None
    legacy_self_run_dir: Path | None = None  # for migration shim

    @property
    def _json_path(self) -> Path:
        return self.seed_run_dir / "role-sessions.json"

    def _read_json(self) -> dict[str, str]:
        if not self._json_path.exists():
            return {}
        try:
            data = json.loads(self._json_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        return dict(data.get("sessions", {})) if isinstance(data, dict) else {}

    def _write_json(self, sessions: dict[str, str]) -> None:
        self.seed_run_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._json_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"version": 1, "sessions": sessions}, indent=2))
        tmp.replace(self._json_path)

    def _read_beads(self) -> dict[str, str]:
        if not _bd_available():
            return {}
        try:
            row = _bd_show(self.seed_id, rig_path=self.rig_path) or {}
        except Exception:
            return {}
        meta = row.get("metadata") or {}
        return {k[len("session_"):]: v for k, v in meta.items() if k.startswith("session_")}

    def _read_legacy(self) -> dict[str, str]:
        if self.legacy_self_run_dir is None:
            return {}
        path = self.legacy_self_run_dir / "metadata.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {k[len("session_"):]: v for k, v in data.items() if k.startswith("session_")}

    def get(self, role: str) -> str | None:
        beads = self._read_beads()
        if role in beads:
            return beads[role]
        local = self._read_json()
        if role in local:
            return local[role]
        legacy = self._read_legacy()
        return legacy.get(role)

    def set(self, role: str, uuid: str) -> None:
        if _bd_available() and self.seed_id:
            # Best-effort: succeed-only-if-bead-exists. _bd_show as probe.
            if _bd_show(self.seed_id, rig_path=self.rig_path) is not None:
                BeadsStore(parent_id=self.seed_id, rig_path=self.rig_path).set(
                    f"session_{role}", uuid
                )
                return
        sessions = self._read_json()
        sessions[role] = uuid
        self._write_json(sessions)

    def all(self) -> dict[str, str]:
        out = self._read_legacy()
        out.update(self._read_json())
        out.update(self._read_beads())  # highest precedence overwrites last
        return out
```

---

## Implementation Steps

1. **Verify `bd dep list --direction=down --type=parent-child` semantics**
   on a real parent-child pair in this rig. If direction is reversed
   (parent→child rather than child→parent), flip in `resolve_seed_bead`.
   (Checkpoint: one-shot `bd dep list` on a known child bead matches
   expectation.)
2. Add `resolve_seed_bead` + tests (`tests/test_beads_meta.py`).
3. Add `RoleSessionStore` + unit tests covering tier ordering and shim.
4. Wire `RoleRegistry` to `RoleSessionStore`; preserve old `store` field
   for non-session metadata (no behavior change for `_refresh_handles`
   beyond the read source).
5. Wire `build_registry` seed resolution; update test
   `test_software_dev_pack_path.py` if it asserts on `metadata.json`
   contents (it doesn't — only routing).
6. Update `po sessions` CLI path + `sessions.py:load_role_sessions`.
7. Add the `persist_to` method + a focused test for branch-fork semantics.
8. Run `uv run python -m pytest` — full unit suite green.
9. Manual e2e (out-of-scope for `software-dev-full` autorun per repo's
   `.po-env`): `po run epic` on a small two-child epic and confirm the
   builder's session UUID matches across both children's
   `metadata.json` (legacy still written) AND the parent's
   `bd show <epic>` metadata.

---

## Testing Strategy

| Layer | Tests |
|---|---|
| unit (`tests/test_beads_meta.py`) | `resolve_seed_bead` happy path (chain), self-seed (no parent), cycle raise, no-bd fallback returns `issue_id`. Use a fake `_bd_dep_list` shim. |
| unit (`tests/test_role_sessions.py`) | Tier ordering: BeadsStore wins over JSON wins over legacy. Migration shim: write only to legacy file, read finds it, subsequent `set` writes to BeadsStore (not legacy). Atomic write: induce KeyboardInterrupt mid-write, file remains valid prior version. |
| unit (`tests/test_role_registry.py`) | Two `RoleRegistry` instances on different children of same parent share builder UUID through real `RoleSessionStore` (mocked `bd`). `persist_to(role, seed_id)` writes elsewhere; original seed's map unchanged. |
| unit (`tests/test_sessions.py`) | `load_role_sessions` unions correctly across legacy + new sources. |
| e2e (manual, gated on `.po-env PO_SKIP_E2E=1`) | Two-child epic run; verify builder session resume on second child via Claude transcript "I previously did X" reference. |

No mocked LLM calls — test surface is the metadata layer, which is real
file/bd I/O wrapped against in-test fakes.

---

## Verification Strategy (acceptance criteria → checks)

| AC | Check |
|---|---|
| **(a)** parent → child1 → child2 reuses `--resume <uuid>` | `tests/test_role_registry.py::test_seed_inheritance_across_children` — two `RoleRegistry`s on `P.1`/`P.2` with seed `P`; `reg1.get("builder").session_id = "abc"`; `reg1.persist("builder")`; `reg2.get("builder").session_id == "abc"`. Plus manual e2e: run two children of an epic, `bd show <epic>` shows `session_builder` populated, second run's `claude` invocation includes `--resume <uuid>` (verify via `po sessions <child2>` showing the same UUID as `<child1>`). |
| **(b)** Fork preserved on critic-spawn-new-branch | `tests/test_role_registry.py::test_persist_to_new_seed_does_not_pollute_original` — `reg.persist_to("critic", seed_id="branch-X")` after a `prompt(fork=True)`; assert original seed's `RoleSessionStore.get("critic")` returns the pre-fork UUID, branch seed's returns the post-fork UUID. |
| **(c)** Migration shim — legacy per-bead `metadata.json` readable | `tests/test_role_sessions.py::test_legacy_metadata_json_shim` — pre-seed `<run_dir>/metadata.json` with `{"session_builder": "legacy-uuid"}`; new `RoleSessionStore.get("builder") == "legacy-uuid"`. Plus follow-up: `set("builder", "new-uuid")` does NOT mutate legacy file (write goes to BeadsStore or new JSON; legacy is read-only shim). |
| **(d)** `sessions.py` + `agent_session.py` + `RoleRegistry` wired | grep check: `grep -n "session_<role>\|session_{role}\|store.get(f\"session_" prefect_orchestration/` shows results only in (i) migration-shim helpers, (ii) `RoleSessionStore` internals — no remaining direct `store.get(f"session_{role}")` in `RoleRegistry`. `tests/test_sessions.py::test_load_role_sessions_unioned`. |

---

## Design Decisions

1. **Separate `role-sessions.json` over reusing `metadata.json`.** Per-run
   `metadata.json` is forensic; mixing shared state confuses retry/resume.
   Cost: one extra file. Benefit: clean read/write boundaries and
   `po artifacts` output stays per-run.
2. **Seed bead = topmost parent-child ancestor, not "first explicit
   parent_bead".** Caller-supplied `parent_bead` still wins (back-compat
   with epic/graph), but the implicit walk handles solo `po run` of a
   nested child correctly.
3. **`BeadsStore` is the primary write tier when bd is available.**
   Visible via `bd show`, integrates with the existing `epic`/`graph`
   behavior, no second source of truth. JSON file is the offline
   fallback only.
4. **`persist_to` is opt-in for forks.** The 90% case (iterations of a
   single role) needs no decision from the formula author. The 10% case
   (branch fork) makes the new-seed an explicit argument — no heuristic
   that could silently break inheritance.
5. **No new entry-point group.** This is composition over `MetadataStore`
   + `bd dep` (principles §5).
6. **`AgentSession` is unchanged.** Session UUID lifecycle stays a
   per-instance concern; only the persistence layer behind
   `RoleRegistry` changes. AC (d) "agent_session.py wired" is satisfied
   by the indirect rewire via `RoleRegistry` — nothing in `agent_session.py`
   needs editing because it never read/wrote storage directly.

---

## Risks

- **`bd dep list` direction confusion.** If `--direction=down --type=parent-child`
  returns descendants rather than ancestors, the seed walker silently
  walks the wrong way. Mitigation: step 1 of implementation is a
  one-shot verification on a real bead pair in this rig before writing
  any code; encode the verified direction as a comment + a unit test
  asserting the direction with a fake `_bd_dep_list`.
- **Multiple parents.** bd allows multi-parent. Plan picks
  `sorted(...)[0]` for determinism. If two parents are both meaningful
  formula seeds, this is wrong silently. Real-world impact: low (we
  haven't seen multi-parent in any rig); accept and revisit if it bites.
- **`persist_to` mis-use.** A formula author who calls `persist` after a
  forked turn pollutes the inheritance chain with the fork's UUID.
  Mitigation: docstring on `AgentSession.prompt(fork=True)` warns "use
  `reg.persist_to(role, branch_seed)` to keep the original chain
  intact"; add a runtime DeprecationWarning if `persist(role)` is called
  immediately after a `fork=True` turn (track via `AgentSession._last_was_fork`).
- **Race: two concurrent flows on different children of the same seed
  call `set("builder", uuid)` simultaneously.** `BeadsStore.set` is a
  `bd update` shellout; `bd` serializes via dolt-server (this rig's
  default). Last-writer-wins is fine — both UUIDs are valid resumes;
  whichever lands second is the canonical chain head. JSON-file fallback
  uses temp+rename so file is never half-written, but two concurrent
  writers on the same JSON can lose updates. Acceptable trade-off
  (fallback is for no-bd dev work, not multi-flow production).
- **`po sessions` semantics shift.** Today the CLI says "session UUIDs
  for this run-dir." Post-change it says "session UUIDs reachable from
  this bead's seed." For solo runs (seed = self), output is identical.
  For epic-child runs, output now matches what's actually used by the
  next child — strictly more useful, but document the change in the
  command's `--help` and a one-line README note.
- **What counts as "same formula"?** The seed-walker doesn't filter by
  formula; if `P` is a `software-dev-full` parent and `P.1` is dispatched
  via a different formula `bio-experiment`, both share the role-sessions
  map. Likely fine — Claude doesn't care which formula spawned the
  prior turn — but mention in design doc that this is intentional and
  worth re-examining if a multi-formula pack ships.

---

## Questions and Clarifications

1. **Q:** Should `role-sessions.json` live under the seed bead's run-dir
   (`<rig>/.planning/<formula>/<seed>/role-sessions.json`) or somewhere
   formula-agnostic (e.g. `<rig>/.planning/role-sessions/<seed>.json`)?
   - **Current approach:** under formula run-dir. Cleaner co-location
     with other per-bead artifacts.
   - **Alternative:** formula-agnostic, since sessions could in principle
     cross formulas (see last risk).
   - **Recommendation:** under formula run-dir for v1; promote to
     formula-agnostic the day a multi-formula seed becomes a real use
     case. Pluggable via `RoleSessionStore.seed_run_dir` constructor arg.
2. **Q:** Should the migration shim ever *promote* legacy values (write
   them forward on read-hit)?
   - **Current approach:** No — promote only on next `set`. Avoids
     write-on-read surprises in `po sessions` (read-only command).
   - **Alternative:** Eager promote on first `get` to consolidate state.
   - **Recommendation:** keep lazy. `po sessions` should never mutate.
3. **Q:** Should `RoleRegistry` keep the legacy `store` field at all once
   sessions move out, or should non-session metadata also migrate?
   - **Current approach:** keep — `_refresh_handles` and `links.md` may
     read other keys later; preserve seam.
   - **Alternative:** drop, since today no other keys are written.
   - **Recommendation:** keep as a no-cost optionality.

---

## Review History

### Iteration 1 — self-review (no external reviewer available in env; performing critical pass)

**Findings + plan amendments:**

1. **`po retry --keep-sessions` unaccounted for** (`cli.py:747`,
   `retry.py`). Today this flag preserves session UUIDs across a
   run-dir archive by keeping `metadata.json` content. Post-change,
   sessions live on the seed (BeadsStore) or in `<seed_run>/role-sessions.json`,
   neither of which is touched by archiving `<self_run>/`. **Amendment:**
   `--keep-sessions` becomes a no-op for the new path (sessions are not
   in the archived dir) but the help text + retry docstring must say
   so explicitly; the legacy-shim path still honors the flag (when
   `metadata.json` had session keys, archiving used to lose them — now
   the shim reads them post-archive too, so the flag is implicitly
   honored). Add `prefect_orchestration/retry.py` to the file-change
   list with a docstring + `--help` update; no behavior code change.

2. **Key-format reconciliation in `sessions.build_rows`.** Old
   `metadata` dict has prefixed keys `session_<role>`; `_role_from_key`
   strips the prefix. New `load_role_sessions` returns bare-role keys.
   **Amendment:** `load_role_sessions` returns the *prefixed* form
   `{"session_builder": uuid, ...}` to match the existing shape that
   `build_rows` and `lookup_session` expect — zero changes to row
   builder / lookup. Internal `RoleSessionStore.get/set` keeps the
   bare-role API; conversion happens at the boundary in `sessions.py`.
   Updated skeleton accordingly (mental note; the file's prose already
   says `session_<role>` for the unioned dict shape — clarify in code
   comments during build).

3. **Overlap with `_resolve_tmux_scope`.** `role_registry.py:207-236`
   already walks `bd show <issue>.metadata.{parent,epic,epic_id,parent_id}`
   for tmux scoping. That's a *different* lookup (scans bd JSON keys
   on the *issue itself*, not `bd dep` edges) and stops at one hop.
   **Amendment:** Don't unify — they answer different questions
   (tmux-scope is "what's the immediate epic for grouping"; seed-bead
   is "topmost ancestor for session affinity"). Add a comment in
   `resolve_seed_bead` cross-referencing the distinction so future
   readers don't conflate.

4. **`AgentSession._last_was_fork` instrumentation** (mentioned under
   Risks) is a forward-looking nice-to-have, not required for AC.
   Demote to a follow-up issue; do not implement in 7vs.2.

5. **Verification of bd-dep direction (risks bullet 1) is mandatory
   step 1, not a probe.** Promoted: implementation step 1 is now a
   blocking checkpoint — write a 5-line script that prints
   `bd dep list <known-child> --direction=down --type=parent-child --json`
   and `bd dep list <known-parent> --direction=up --type=parent-child --json`,
   compare against expectation, encode the verified direction with a
   citation in `resolve_seed_bead`'s docstring.

**Verdict:** APPROVED with the five amendments above folded in. Plan
is implementation-ready.

