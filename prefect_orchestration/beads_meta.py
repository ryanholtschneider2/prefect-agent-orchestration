"""Minimal `bd` CLI wrapper for parent-molecule metadata.

The software-dev-full formula uses beads metadata as the shared state
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
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol


class MetadataStore(Protocol):
    def get(self, key: str, default: str | None = None) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def all(self) -> dict[str, str]: ...


@dataclass
class BeadsStore:
    """Reads/writes metadata on a beads parent molecule."""

    parent_id: str

    def get(self, key: str, default: str | None = None) -> str | None:
        out = subprocess.run(
            ["bd", "show", self.parent_id, "--json"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        meta = json.loads(out).get("metadata") or {}
        return meta.get(key, default)

    def set(self, key: str, value: str) -> None:
        subprocess.run(
            ["bd", "update", self.parent_id, "--set-metadata", f"{key}={value}"],
            check=True,
        )

    def all(self) -> dict[str, str]:
        out = subprocess.run(
            ["bd", "show", self.parent_id, "--json"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return json.loads(out).get("metadata") or {}


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


def auto_store(parent_id: str | None, run_dir: Path) -> MetadataStore:
    """Use beads if available and parent_id given; else file store."""
    if parent_id and shutil.which("bd"):
        return BeadsStore(parent_id=parent_id)
    return FileStore(path=run_dir / "metadata.json")


def _bd_available() -> bool:
    return shutil.which("bd") is not None


def claim_issue(issue_id: str, assignee: str) -> None:
    """Mark a beads issue in_progress + claim it. No-op if bd missing."""
    if not _bd_available():
        return
    subprocess.run(
        ["bd", "update", issue_id, "--status", "in_progress", "--assignee", assignee],
        check=False,
    )


def close_issue(issue_id: str, notes: str | None = None) -> None:
    """Close a beads issue. No-op if bd missing."""
    if not _bd_available():
        return
    cmd = ["bd", "close", issue_id]
    if notes:
        cmd += ["--reason", notes]
    subprocess.run(cmd, check=False)


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
) -> list[dict]:
    """Run `bd dep list <id> --direction=<dir> [--type=<t>] --json`.

    Returns [] on any non-zero exit or empty body — bd has been observed
    to print "No issues depend on …" to stdout while exiting 0 with no
    JSON, so we tolerate `JSONDecodeError` too.
    """
    if not _bd_available():
        return []
    cmd = ["bd", "dep", "list", issue_id, f"--direction={direction}", "--json"]
    if edge_type is not None:
        cmd += ["--type", edge_type]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    try:
        rows = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    return rows if isinstance(rows, list) else []


def _bd_show(issue_id: str) -> dict | None:
    """Return the bd show row for a single issue, or None if not found."""
    if not _bd_available():
        return None
    proc = subprocess.run(
        ["bd", "show", issue_id, "--json"],
        capture_output=True,
        text=True,
        check=False,
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


def list_subgraph(
    root_id: str,
    traverse: str | Iterable[str] = DEFAULT_TRAVERSE,
    *,
    include_closed: bool = False,
    include_root: bool = False,
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
        root_row = _bd_show(root_id)
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
            for row in _bd_dep_list(cur, direction="up", edge_type=et):
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
        deps_rows = _bd_dep_list(cid, direction="down", edge_type="blocks")
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


def list_epic_children(epic_id: str) -> list[dict]:
    """Return [{'id', 'status', 'dependencies': [id,...]}, ...] for an epic's children.

    Beads epic→child link is by **ID prefix convention** (`<epic>.<N>`), not
    a `parent_id` field — `bd list --parent` returns empty for most epics.
    We probe `<epic>.1`, `<epic>.2`, ... sequentially until we hit a gap.

    Only returns open/in_progress children; closed ones are already done.
    """
    if not _bd_available():
        return []
    children = []
    consecutive_missing = 0
    n = 0
    while consecutive_missing < 3:
        n += 1
        candidate = f"{epic_id}.{n}"
        proc = subprocess.run(
            ["bd", "show", candidate, "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            consecutive_missing += 1
            continue
        consecutive_missing = 0
        try:
            rows = json.loads(proc.stdout)
            row = rows[0] if isinstance(rows, list) else rows
        except (json.JSONDecodeError, IndexError):
            continue
        if row.get("status") in ("open", "in_progress"):
            deps = [
                d["id"] if isinstance(d, dict) else d
                for d in row.get("dependencies") or []
            ]
            children.append(
                {
                    "id": row["id"],
                    "status": row["status"],
                    "dependencies": deps,
                    "title": row.get("title", ""),
                }
            )
    return children
