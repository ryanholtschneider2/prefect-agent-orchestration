"""Minimal `bd` CLI wrapper for parent-molecule metadata.

Formulas use beads metadata as the shared state
bus between steps (iter counters, verdicts, run_dir, feature flags).
We mirror that here so role prompts that read `bd show <parent>` work
unchanged.

For prototype/local runs without beads installed, `FileStore` falls
back to a JSON file under `$RUN_DIR/metadata.json`.
"""

from __future__ import annotations

import graphlib
import json
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal, Protocol

DiscoverMode = Literal["ids", "deps", "both"]
VALID_DISCOVER_MODES: tuple[str, ...] = ("ids", "deps", "both")


class MetadataStore(Protocol):
    def get(self, key: str, default: str | None = None) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def all(self) -> dict[str, str]: ...


@dataclass
class BeadsStore:
    """Reads/writes metadata on a beads parent molecule.

    `rig_path` is the directory the bd binary should resolve `.beads/`
    from. When set, every shellout passes `cwd=str(rig_path)`. When
    `None`, the call inherits the Python process cwd — preserves
    backward compatibility for ad-hoc callers without a rig.
    """

    parent_id: str
    rig_path: Path | str | None = None

    def _cwd(self) -> str | None:
        return str(self.rig_path) if self.rig_path is not None else None

    def _show_metadata(self) -> dict[str, str]:
        out = subprocess.run(
            ["bd", "show", self.parent_id, "--json"],
            capture_output=True,
            text=True,
            check=True,
            cwd=self._cwd(),
        ).stdout
        parsed = json.loads(out)
        # Some bd versions return a single-row JSON list; others return
        # a bare dict. Normalise to dict before reading metadata.
        row = parsed[0] if isinstance(parsed, list) and parsed else parsed
        if not isinstance(row, dict):
            return {}
        return row.get("metadata") or {}

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._show_metadata().get(key, default)

    def set(self, key: str, value: str) -> None:
        subprocess.run(
            ["bd", "update", self.parent_id, "--set-metadata", f"{key}={value}"],
            check=True,
            cwd=self._cwd(),
        )

    def all(self) -> dict[str, str]:
        return self._show_metadata()


@dataclass
class FileStore:
    """Local-file fallback: `$RUN_DIR/metadata.json`."""

    path: Path

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def _dump(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2))

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._load().get(key, default)

    def set(self, key: str, value: str) -> None:
        data = self._load()
        data[key] = value
        self._dump(data)

    def all(self) -> dict[str, str]:
        return self._load()


def auto_store(
    parent_id: str | None,
    run_dir: Path,
    rig_path: Path | str | None = None,
) -> MetadataStore:
    """Use beads if available and parent_id given; else file store.

    When `rig_path` is supplied, the constructed `BeadsStore` carries it
    so every bd shellout runs with `cwd=rig_path`. Required when the
    Python process cwd is not the rig (e.g. Prefect task runner).
    """
    if parent_id and shutil.which("bd"):
        return BeadsStore(parent_id=parent_id, rig_path=rig_path)
    return FileStore(path=run_dir / "metadata.json")


def _bd_available() -> bool:
    # cwd-independent: shutil.which is PATH-based.
    return shutil.which("bd") is not None


def claim_issue(
    issue_id: str,
    assignee: str,
    rig_path: Path | str | None = None,
) -> None:
    """Mark a beads issue in_progress + claim it. No-op if bd missing.

    `rig_path` (when set) becomes the bd shellout's `cwd` so it targets
    the rig's `.beads/` rather than the Python process cwd.
    """
    if not _bd_available():
        return
    subprocess.run(
        ["bd", "update", issue_id, "--status", "in_progress", "--assignee", assignee],
        check=False,
        cwd=str(rig_path) if rig_path is not None else None,
    )


def create_child_bead(
    parent_id: str,
    child_id: str,
    *,
    title: str,
    description: str,
    issue_type: str = "task",
    rig_path: Path | str | None = None,
    priority: int = 2,
    blocks: str | None = None,
) -> str:
    """Create a child bead with explicit id + parent edge.

    Idempotent: if `child_id` already exists, returns it without
    error. Returns the child id on success. NotImplementedError if
    `bd` is missing (FileStore has no graph support — the
    bead-mediated handoff requires bd).

    Shells `bd create --id=<child_id> --parent=<parent_id> --title=...
    --description=... --type=<type> -p <priority>` with
    `cwd=rig_path`. On id collision (`bd` exits non-zero with
    "already exists" stderr) we treat the call as a successful no-op
    so callers retrying (Prefect task retry, ralph re-entry) reuse
    the existing bead's state instead of erroring.

    `blocks` (when set) emits `--deps blocks:<id>` so the new bead
    is recorded as blocked-by `<id>` (the prior iter). Best-effort:
    on the idempotent already-exists path the dep edge is NOT
    re-applied (bd would no-op, but we don't shell out at all to
    avoid a second call). If the dep edge needs to be added after
    the fact, callers should `bd dep add` directly.
    """
    if not _bd_available():
        raise NotImplementedError(
            "create_child_bead requires the `bd` CLI on PATH "
            "(FileStore has no graph support)."
        )
    # bd 1.0 rejects `--id` + `--parent` together; the working alternative
    # is to express the parent edge via `--deps parent-child:<id>`.
    deps = [f"parent-child:{parent_id}"]
    if blocks:
        deps.append(f"blocks:{blocks}")
    cmd = [
        "bd",
        "create",
        f"--id={child_id}",
        "--title",
        title,
        "--description",
        description,
        "--type",
        issue_type,
        "-p",
        str(priority),
        "--deps",
        ",".join(deps),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(rig_path) if rig_path is not None else None,
    )
    if proc.returncode == 0:
        return child_id
    stderr = (proc.stderr or "") + (proc.stdout or "")
    if "already exists" in stderr.lower():
        return child_id
    raise RuntimeError(
        f"bd create {child_id} failed (rc={proc.returncode}): {stderr.strip()}"
    )


def close_issue(
    issue_id: str,
    notes: str | None = None,
    rig_path: Path | str | None = None,
) -> None:
    """Close a beads issue. No-op if bd missing.

    `rig_path` (when set) becomes the bd shellout's `cwd`.
    """
    if not _bd_available():
        return
    cmd = ["bd", "close", issue_id]
    if notes:
        cmd += ["--reason", notes]
    subprocess.run(
        cmd,
        check=False,
        cwd=str(rig_path) if rig_path is not None else None,
    )


def read_iter_cap(
    parent_id: str,
    default: int,
    *,
    rig_path: Path | str | None = None,
    metadata_key: str = "po.iter_cap",
) -> int:
    """Return the positive-int iter cap for *parent_id*, or *default* when unset/invalid.

    Looks up `metadata_key` (default `"po.iter_cap"`) in the parent's
    bd metadata. Falls back to `default` when:

    - `bd` is not on PATH (FileStore path / no-bd dev),
    - the parent has no such metadata key,
    - or the value isn't a positive int (non-numeric, zero, negative).

    The kwarg-fallback shape preserves backwards compatibility for
    callers that still pass `iter_cap=N` to a flow — per-bead override
    via `bd update <id> --set-metadata po.iter_cap=N` wins when set,
    otherwise the kwarg default sticks.
    """
    if not _bd_available() or not parent_id:
        return default
    row = _bd_show(parent_id, rig_path=rig_path)
    if row is None:
        return default
    metadata = row.get("metadata") or {}
    if not isinstance(metadata, dict):
        return default
    raw = metadata.get(metadata_key)
    if raw is None:
        return default
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed


# ─────────────────────── graph traversal ────────────────────────────
#
# `list_subgraph` + `topo_sort_blocks` back the `graph_run` formula
# (prefect-orchestration-uc0). They walk `bd dep` edges to collect a
# sub-graph rooted at any bead — no naming convention required —
# and produce a topo-ordered list ready for Prefect `wait_for=`
# fan-out.

VALID_EDGE_TYPES: tuple[str, ...] = ("parent-child", "blocks", "tracks")
DEFAULT_TRAVERSE: tuple[str, ...] = ("parent-child", "blocks")


def _normalize_traverse(traverse: str | Iterable[str]) -> tuple[str, ...]:
    """Coerce the `traverse` arg to a validated tuple of edge types.

    Accepts `"parent-child,blocks"` (CLI form) or any iterable of
    strings. Raises `ValueError` on an unknown edge type so the caller
    fails before any bd shellouts.
    """
    if isinstance(traverse, str):
        tokens = [t.strip() for t in traverse.split(",") if t.strip()]
    else:
        tokens = [t.strip() for t in traverse if t and t.strip()]
    bad = [t for t in tokens if t not in VALID_EDGE_TYPES]
    if bad:
        raise ValueError(
            f"unknown edge type(s) {bad!r}; valid: {list(VALID_EDGE_TYPES)}"
        )
    if not tokens:
        raise ValueError("traverse must include at least one edge type")
    # Preserve order, drop dupes.
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            out.append(t)
            seen.add(t)
    return tuple(out)


def _bd_dep_list(
    issue_id: str,
    direction: str,
    edge_type: str | None = None,
    rig_path: Path | str | None = None,
) -> list[dict]:
    """Return the dep-graph rows for *issue_id* as a list of dicts (empty on failure).

    Shells out to `bd dep list <id> --direction=<dir> [--type=<t>] --json`.
    Returns [] on any non-zero exit or empty body — bd has been observed
    to print "No issues depend on …" to stdout while exiting 0 with no
    JSON, so we tolerate `JSONDecodeError` too.

    `rig_path` (when set) becomes the shellout's `cwd` so bd resolves the
    rig's `.beads/` instead of the Python process cwd.
    """
    if not _bd_available():
        return []
    cmd = ["bd", "dep", "list", issue_id, f"--direction={direction}", "--json"]
    if edge_type is not None:
        cmd += ["--type", edge_type]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(rig_path) if rig_path is not None else None,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    try:
        rows = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    return rows if isinstance(rows, list) else []


def _bd_show(
    issue_id: str,
    rig_path: Path | str | None = None,
) -> dict | None:
    """Return the bd show row for a single issue, or None if not found.

    `rig_path` (when set) becomes the shellout's `cwd`.
    """
    if not _bd_available():
        return None
    proc = subprocess.run(
        ["bd", "show", issue_id, "--json"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(rig_path) if rig_path is not None else None,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        rows = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if isinstance(rows, list):
        return rows[0] if rows else None
    return rows if isinstance(rows, dict) else None


def resolve_seed_bead(
    issue_id: str,
    rig_path: Path | str | None = None,
    *,
    max_hops: int = 16,
) -> str:
    """Topmost ancestor reachable via parent-child edges (or `issue_id`).

    Walks `bd dep list <cur> --direction=down --type=parent-child` upward.
    On this rig (verified 2026-04-28 against 7vs.2 ↔ 7vs):
    `--direction=down --type=parent-child` returns *parents* (ancestors)
    of the queried bead; `--direction=up` returns *children*. We use
    `down` to walk to the topmost ancestor.

    Returns `issue_id` itself when:
      - `bd` is not on PATH (no graph to walk → solo-run / FileStore path)
      - the issue has no parent-child parent (it IS a seed)

    Cycle guard: caps at `max_hops` (default 16); raises `ValueError` if
    a cycle or absurdly deep chain is detected.

    Distinct from `_resolve_tmux_scope` (in `role_registry`) which reads
    `bd show <issue>.metadata.{parent,epic,...}` for one-hop tmux
    grouping. This walks the dep graph for session-affinity seed
    resolution; do not unify.
    """
    if not _bd_available():
        return issue_id
    cur = issue_id
    seen: set[str] = {cur}
    for _ in range(max_hops):
        parents = _bd_dep_list(
            cur, direction="down", edge_type="parent-child", rig_path=rig_path
        )
        if not parents:
            return cur
        # parent-child edges from a child point at exactly one parent in
        # the canonical case; tolerate >1 deterministically by sorted
        # first id (matches plan §Risks "Multiple parents").
        candidate_ids = sorted(p["id"] for p in parents if p.get("id"))
        if not candidate_ids:
            return cur
        nxt = candidate_ids[0]
        if nxt in seen:
            raise ValueError(f"parent-child cycle through {cur}->{nxt}")
        seen.add(nxt)
        cur = nxt
    raise ValueError(f"parent-child chain exceeds {max_hops} hops from {issue_id}")


def list_subgraph(
    root_id: str,
    traverse: str | Iterable[str] = DEFAULT_TRAVERSE,
    *,
    include_closed: bool = False,
    include_root: bool = False,
    rig_path: Path | str | None = None,
) -> list[dict]:
    """BFS the bd-dep graph rooted at `root_id`; return collected nodes.

    Each returned node is a dict with at least::

        {"id": str, "status": str, "title": str,
         "block_deps": [<id>, ...]}     # ids of blockers within the set

    `traverse` is a comma-separated string ("parent-child,blocks") or an
    iterable of edge-type strings. Default is ("parent-child", "blocks").
    Edge directions: BFS follows edges *up* (i.e. for each visited node,
    we ask "what depends on me with this edge type?") so we discover
    descendants of the root.

    `include_closed=False` skips `closed` nodes (the common case: don't
    re-run finished work). `include_root=False` excludes the root from
    the returned set (it's a container, not a runnable node) — useful
    when the root is the epic / convoy / grouping bead.

    BFS traverses *through* closed intermediate nodes; closed nodes are
    dropped from the final set (unless `include_closed=True`) but their
    open descendants are still discovered. This lets you re-run the
    open tail of a half-finished chain without manually re-rooting.

    The returned `block_deps` list contains only ids that are *also* in
    the collected set — out-of-set deps don't need to be waited on
    (closed deps are already done; unrelated deps are bd's problem,
    not Prefect's).
    """
    edge_types = _normalize_traverse(traverse)

    # BFS via per-(node, edge-type) `bd dep list --direction=up` shellouts.
    visited: set[str] = {root_id}
    collected: dict[str, dict] = {}
    queue: deque[str] = deque([root_id])

    if include_root:
        root_row = _bd_show(root_id, rig_path=rig_path)
        if root_row is not None:
            root_status = root_row.get("status", "open")
            # Skip the row when it would be filtered out below — saves
            # the dict copy for the closed-root + !include_closed path.
            if include_closed or root_status != "closed":
                collected[root_id] = {
                    "id": root_row["id"],
                    "status": root_status,
                    "title": root_row.get("title", ""),
                }

    while queue:
        cur = queue.popleft()
        for et in edge_types:
            for row in _bd_dep_list(
                cur, direction="up", edge_type=et, rig_path=rig_path
            ):
                rid = row.get("id")
                if not rid or rid in visited:
                    continue
                visited.add(rid)
                queue.append(rid)
                collected[rid] = {
                    "id": rid,
                    "status": row.get("status", "open"),
                    "title": row.get("title", ""),
                }

    # Status filter.
    if not include_closed:
        collected = {cid: c for cid, c in collected.items() if c["status"] != "closed"}
    # If `include_root` is on but the root was closed and `include_closed`
    # is off, the root drops out of `collected` here — which matches the
    # conservative "treat root like any other node" reading.

    if not collected:
        return []

    # Build the blocks-only sub-DAG: for each collected node, ask bd
    # what *it* depends on via --type=blocks (direction=down). Keep
    # only deps that are in the collected set.
    in_set = set(collected)
    for cid, node in collected.items():
        deps_rows = _bd_dep_list(
            cid, direction="down", edge_type="blocks", rig_path=rig_path
        )
        node["block_deps"] = [r["id"] for r in deps_rows if r.get("id") in in_set]

    return list(collected.values())


def topo_sort_blocks(nodes: list[dict]) -> list[dict]:
    """Topologically sort `nodes` by their `block_deps` edges.

    Each node must carry a `block_deps: list[str]` field — ids of
    blockers within the set, as produced by `list_subgraph`. Raises
    ``ValueError("dependency cycle: [ids...]")`` if the blocks-subgraph
    contains a cycle (AC 2 of prefect-orchestration-uc0). Cycle members
    are extracted from `graphlib.CycleError` so the error names the
    actual cycle, not just the unsorted residue.

    Falls back to id order for ties so test assertions are stable.
    """
    if not nodes:
        return []
    by_id = {n["id"]: n for n in nodes}
    ts: graphlib.TopologicalSorter[str] = graphlib.TopologicalSorter()
    for n in nodes:
        ts.add(n["id"], *(d for d in n.get("block_deps", []) if d in by_id))
    try:
        order = list(ts.static_order())
    except graphlib.CycleError as exc:
        # CycleError.args == ("cycle in static order", [n1, n2, n1])
        # The trailing list is the cycle path with the start vertex
        # repeated; dedupe while preserving order so the message is
        # readable.
        cycle_path = exc.args[1] if len(exc.args) > 1 else []
        seen: set[str] = set()
        cycle_ids: list[str] = []
        for cid in cycle_path:
            if cid not in seen:
                cycle_ids.append(cid)
                seen.add(cid)
        raise ValueError(f"dependency cycle: {cycle_ids}") from exc
    return [by_id[i] for i in order]


def _dot_suffix_children(
    epic_id: str,
    rig_path: Path | str | None = None,
) -> list[dict]:
    """Probe `<epic>.1`, `<epic>.2`, … until 3 consecutive misses.

    Returns graph-shape rows `{id, status, title, block_deps}` with
    `block_deps` restricted to ids also in the discovered set (same
    contract `list_subgraph` produces, so the result is topo-sortable
    by `topo_sort_blocks` directly). Skips closed children.

    `rig_path` (when set) becomes the `bd show` shellout's `cwd`.
    """
    if not _bd_available():
        return []
    raw: list[tuple[dict, list[str]]] = []
    consecutive_missing = 0
    n = 0
    cwd = str(rig_path) if rig_path is not None else None
    while consecutive_missing < 3:
        n += 1
        candidate = f"{epic_id}.{n}"
        proc = subprocess.run(
            ["bd", "show", candidate, "--json"],
            capture_output=True,
            text=True,
            check=False,
            cwd=cwd,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            consecutive_missing += 1
            continue
        consecutive_missing = 0
        try:
            parsed = json.loads(proc.stdout)
            row = parsed[0] if isinstance(parsed, list) else parsed
        except (json.JSONDecodeError, IndexError):
            continue
        if row.get("status") in ("open", "in_progress"):
            deps = [
                d["id"] if isinstance(d, dict) else d
                for d in row.get("dependencies") or []
            ]
            raw.append(
                (
                    {
                        "id": row["id"],
                        "status": row["status"],
                        "title": row.get("title", ""),
                    },
                    deps,
                )
            )
    in_set = {node["id"] for node, _ in raw}
    return [
        {**node, "block_deps": [d for d in deps if d in in_set]} for node, deps in raw
    ]


def list_epic_children(
    epic_id: str,
    mode: DiscoverMode = "ids",
    rig_path: Path | str | None = None,
) -> list[dict]:
    """Return graph-shape children of an epic; discovery driven by `mode`.

    Each returned node is `{id, status, title, block_deps}`, matching
    the shape `list_subgraph` produces — so callers can topo-sort the
    result with `topo_sort_blocks` directly.

    `mode` selects the discovery strategy (default `"ids"`, which preserves
    the historical dot-suffix probe behaviour for back-compat):

    - ``"ids"``  — probe `<epic>.1`, `<epic>.2`, … (gas-city convention).
      Fast, no `bd dep` graph required.
    - ``"deps"`` — walk the `bd dep` graph (parent-child + blocks edges)
      via `list_subgraph`. Works for any connected sub-graph; no naming
      convention required.
    - ``"both"`` — union of `deps` and `ids` with stable de-dup. `deps`
      order first (BFS from `list_subgraph`), then any dot-suffix-only
      ids appended. `block_deps` for shared ids unions both sources;
      no merged-set re-restriction is needed because each source
      already restricts its own `block_deps` to its in-set, and the
      merged set is a superset of each.

    Closed beads are filtered out in every mode (matches the original
    `list_epic_children` semantics + `list_subgraph(include_closed=False)`).
    """
    if mode not in VALID_DISCOVER_MODES:
        raise ValueError(
            f"unknown discover mode {mode!r}; valid: {list(VALID_DISCOVER_MODES)}"
        )
    if mode == "ids":
        return _dot_suffix_children(epic_id, rig_path=rig_path)
    deps_nodes = list_subgraph(
        epic_id,
        traverse=("parent-child", "blocks"),
        include_closed=False,
        include_root=False,
        rig_path=rig_path,
    )
    if mode == "deps":
        return deps_nodes

    # mode == "both": deps order first, then dot-suffix-only ids appended.
    # Both sources already restrict block_deps to their own in-set, and
    # the merged set is a superset of each — so per-node union is enough
    # without a final merged-set re-restriction.
    ids_nodes = _dot_suffix_children(epic_id, rig_path=rig_path)
    if not deps_nodes:
        return ids_nodes
    if not ids_nodes:
        return deps_nodes

    by_id: dict[str, dict] = {}
    order: list[str] = []
    for node in deps_nodes:
        by_id[node["id"]] = dict(node)
        order.append(node["id"])
    for node in ids_nodes:
        if node["id"] in by_id:
            existing = by_id[node["id"]].get("block_deps", [])
            incoming = node.get("block_deps", [])
            by_id[node["id"]]["block_deps"] = list(dict.fromkeys(existing + incoming))
        else:
            by_id[node["id"]] = dict(node)
            order.append(node["id"])
    return [by_id[i] for i in order]


def collect_explicit_children(
    child_ids: Iterable[str],
    rig_path: Path | str | None = None,
) -> list[dict]:
    """`--child-ids` override: build graph nodes for an explicit id list.

    Bypasses discovery entirely. For each id:

    - `bd show <id> --json` to confirm existence + capture status/title;
      missing ids raise `ValueError`.
    - Refuse closed ids (consistent with `list_epic_children`'s
      `include_closed=False`); caller must reopen first.
    - Build `block_deps` from `bd dep list <id> --direction=down --type=blocks`,
      restricted to the explicit set.

    Returns `{id, status, title, block_deps}` shape, ready for
    `topo_sort_blocks`.
    """
    ids = [cid.strip() for cid in child_ids if cid and cid.strip()]
    if not ids:
        raise ValueError("child_ids must be non-empty")
    seen: set[str] = set()
    duplicates: list[str] = []
    for cid in ids:
        if cid in seen:
            duplicates.append(cid)
        else:
            seen.add(cid)
    if duplicates:
        raise ValueError(f"duplicate child id(s): {sorted(set(duplicates))}")

    rows: dict[str, dict] = {}
    missing: list[str] = []
    closed: list[str] = []
    for cid in ids:
        row = _bd_show(cid, rig_path=rig_path)
        if row is None:
            missing.append(cid)
            continue
        if row.get("status") == "closed":
            closed.append(cid)
            continue
        rows[cid] = {
            "id": cid,
            "status": row.get("status", "open"),
            "title": row.get("title", ""),
        }
    if missing:
        raise ValueError(f"unknown child id(s): {missing}")
    if closed:
        raise ValueError(
            f"closed child id(s): {closed}; reopen with `bd update <id> --status open`"
        )

    in_set = set(rows)
    for cid, node in rows.items():
        deps = _bd_dep_list(
            cid, direction="down", edge_type="blocks", rig_path=rig_path
        )
        node["block_deps"] = [r["id"] for r in deps if r.get("id") in in_set]

    # Preserve the caller's input order (deterministic; topo-sort then
    # picks up any blocking constraints).
    return [rows[i] for i in ids]


# ─────────────────────── watch primitive ────────────────────────────
#
# `watch()` blocks until any bead in a watched set transitions state.
# Underpins the "bd close = turn-end signal" design from
# prefect-orchestration-7vs (formulas-as-bead-graphs): instead of
# parsing tmux scrollback for an LLM "I'm done" sentinel, the
# orchestrator polls the bead store for a status flip.
#
# Implementation is poll-based (1.5s default) against whatever backend
# `bd` is configured for — embedded-dolt or dolt-server alike. To
# upgrade to push-based delivery later, swap the inner `_snapshot()`
# loop for a dolt change-feed subscription (DOLT 1.x exposes
# `dolt_log` + `dolt_diff` system tables; a long-poll on
# `SELECT * FROM dolt_log WHERE commit_hash > ?` against the bd
# database delivers the same BeadEvent stream without per-poll
# `bd show` shellouts).

DEFAULT_WATCH_POLL_INTERVAL: float = 1.5

WatchEvent = Literal["close", "status", "any"]
VALID_WATCH_EVENTS: tuple[str, ...] = ("close", "status", "any")


@dataclass(frozen=True)
class BeadEvent:
    """A single observed transition on a watched bead.

    `kind` is one of:

    - ``"close"``  — status transitioned to ``"closed"``.
    - ``"status"`` — status changed (and not to ``"closed"``).
    - ``"mutate"`` — `updated_at` advanced with no status change
      (notes / description / metadata edit). Only emitted for
      ``event="any"`` watches.
    """

    bead_id: str
    kind: Literal["close", "status", "mutate"]
    old_status: str | None
    new_status: str | None
    updated_at: str | None
    timestamp: float = field(default_factory=time.time)


def _snapshot(
    bead_ids: Iterable[str],
    rig_path: Path | str | None = None,
) -> dict[str, dict[str, str]]:
    """Return `{id: {"status": ..., "updated_at": ...}}` for each bead.

    Missing beads are dropped (consistent with how `_bd_show` returns
    None on bd errors). Caller treats "missing on first snapshot" as
    `ValueError`; "missing on a later poll" as a no-op.
    """
    out: dict[str, dict[str, str]] = {}
    for bid in bead_ids:
        row = _bd_show(bid, rig_path=rig_path)
        if row is None:
            continue
        out[bid] = {
            "status": row.get("status", "open"),
            "updated_at": row.get("updated_at", "") or "",
        }
    return out


def watch(
    bead_ids: Iterable[str],
    event: WatchEvent = "close",
    timeout: float | None = None,
    *,
    poll_interval: float = DEFAULT_WATCH_POLL_INTERVAL,
    rig_path: Path | str | None = None,
    cancel: threading.Event | None = None,
) -> list[BeadEvent]:
    """Block until at least one watched bead matches `event`, then return.

    Parameters
    ----------
    bead_ids
        Set/iterable of bead ids to watch. Must be non-empty. Each id
        must resolve via `bd show` on entry; unknown ids raise
        ``ValueError``.
    event
        - ``"close"``  (default) — return when any bead transitions to
          status ``"closed"``.
        - ``"status"`` — return on any status change (including close).
        - ``"any"``    — return on any status change OR `updated_at`
          advance (notes/description/metadata edit).
    timeout
        Wall-clock seconds to wait. ``None`` means wait forever.
        Returns ``[]`` on timeout (NOT raises) so callers can
        distinguish "nothing happened" from cancellation.
    poll_interval
        Seconds between `bd show` polls. Default 1.5s. Lower values
        burn bd shellouts; higher values delay wake-up.
    rig_path
        Directory the bd binary should resolve `.beads/` from. Required
        when the Python process cwd is not the rig.
    cancel
        Optional `threading.Event`. If set during a poll, `watch`
        returns ``[]`` immediately. Lets a parent flow tear down a
        watcher without waiting for `timeout`.

    Returns
    -------
    list[BeadEvent]
        All transitions observed in the poll cycle that produced the
        first match. Multiple beads racing on the same poll yield
        multiple events (deterministic order: input order preserved).
        Empty list on timeout or cancellation.

    Raises
    ------
    NotImplementedError
        If `bd` is not on `PATH`. The FileStore (no-bd) backend has no
        change feed; mtime polling on `metadata.json` is a follow-up.
    ValueError
        Empty `bead_ids`, unknown event, unknown bead id on entry, or
        non-positive `poll_interval`.

    Notes
    -----
    Polling interval defaults to 1.5s (`DEFAULT_WATCH_POLL_INTERVAL`).
    To upgrade to push-based delivery, replace `_snapshot()` with a
    dolt change-feed subscription: long-poll
    ``SELECT id, status, updated_at FROM beads WHERE updated_at > ?``
    against the bd database (or `dolt_log`/`dolt_diff` for full
    history) and emit `BeadEvent`s on each row delta. Same return
    shape — keeps callers identical.
    """
    if event not in VALID_WATCH_EVENTS:
        raise ValueError(
            f"unknown watch event {event!r}; valid: {list(VALID_WATCH_EVENTS)}"
        )
    if poll_interval <= 0:
        raise ValueError(f"poll_interval must be > 0, got {poll_interval!r}")
    ids = [bid.strip() for bid in bead_ids if bid and bid.strip()]
    if not ids:
        raise ValueError("bead_ids must be non-empty")
    # De-dupe while preserving input order.
    seen: set[str] = set()
    ordered: list[str] = []
    for bid in ids:
        if bid not in seen:
            seen.add(bid)
            ordered.append(bid)

    if not _bd_available():
        raise NotImplementedError(
            "beads_meta.watch() requires the `bd` CLI on PATH (FileStore "
            "fallback has no change feed; mtime polling on "
            "metadata.json is a follow-up)."
        )

    initial = _snapshot(ordered, rig_path=rig_path)
    missing = [bid for bid in ordered if bid not in initial]
    if missing:
        raise ValueError(f"unknown bead id(s): {missing}")

    deadline = (time.monotonic() + timeout) if timeout is not None else None
    prev = initial

    while True:
        if cancel is not None and cancel.is_set():
            return []
        if deadline is not None and time.monotonic() >= deadline:
            return []

        # Sleep first so we don't spin on the snapshot we already took.
        # Honour cancel/deadline mid-sleep via short slices.
        slept = 0.0
        slice_s = min(0.25, poll_interval)
        while slept < poll_interval:
            if cancel is not None and cancel.is_set():
                return []
            if deadline is not None and time.monotonic() >= deadline:
                return []
            time.sleep(min(slice_s, poll_interval - slept))
            slept += slice_s

        cur = _snapshot(ordered, rig_path=rig_path)
        events: list[BeadEvent] = []
        for bid in ordered:
            before = prev.get(bid)
            after = cur.get(bid)
            if before is None or after is None:
                # Disappeared mid-watch (race with `bd` server) — skip
                # this poll for that bead; will reappear next cycle.
                continue
            old_s = before["status"]
            new_s = after["status"]
            if new_s == "closed" and old_s != "closed":
                events.append(
                    BeadEvent(
                        bead_id=bid,
                        kind="close",
                        old_status=old_s,
                        new_status=new_s,
                        updated_at=after["updated_at"] or None,
                    )
                )
                continue
            if event in ("status", "any") and new_s != old_s:
                events.append(
                    BeadEvent(
                        bead_id=bid,
                        kind="status",
                        old_status=old_s,
                        new_status=new_s,
                        updated_at=after["updated_at"] or None,
                    )
                )
                continue
            if event == "any" and after["updated_at"] != before["updated_at"]:
                events.append(
                    BeadEvent(
                        bead_id=bid,
                        kind="mutate",
                        old_status=old_s,
                        new_status=new_s,
                        updated_at=after["updated_at"] or None,
                    )
                )
        prev = cur
        if events:
            return events
