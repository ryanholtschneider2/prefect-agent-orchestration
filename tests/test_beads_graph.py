"""Unit tests for `beads_meta.list_subgraph` + `topo_sort_blocks`.

Backs `prefect-orchestration-uc0` — the `graph_run` formula's discovery +
ordering primitives. These mock `subprocess.run` to return synthetic
`bd dep list --json` payloads; no real `bd` invocation.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from prefect_orchestration import beads_meta


# ─────────────────────── fake bd ────────────────────────────


class _FakeBd:
    """Records `bd dep list ...` invocations and returns canned JSON.

    Indexed by a tuple key ``(issue_id, direction, edge_type)``; missing
    keys return an empty list. Also tracks `bd show` calls via the
    ``shows`` map (id → row dict).
    """

    def __init__(
        self,
        edges: dict[tuple[str, str, str | None], list[dict]] | None = None,
        shows: dict[str, dict] | None = None,
    ) -> None:
        self.edges = edges or {}
        self.shows = shows or {}
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **_: Any) -> subprocess.CompletedProcess:
        self.calls.append(list(cmd))
        if cmd[:3] == ["bd", "dep", "list"]:
            issue_id = cmd[3]
            direction = next(
                (a.split("=", 1)[1] for a in cmd if a.startswith("--direction=")),
                "down",
            )
            edge_type: str | None = None
            if "--type" in cmd:
                edge_type = cmd[cmd.index("--type") + 1]
            rows = self.edges.get((issue_id, direction, edge_type), [])
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(rows), stderr=""
            )
        if cmd[:2] == ["bd", "show"]:
            issue_id = cmd[2]
            row = self.shows.get(issue_id)
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps([row] if row else []), stderr=""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


@pytest.fixture
def fake_bd(monkeypatch: pytest.MonkeyPatch) -> _FakeBd:
    fake = _FakeBd()
    monkeypatch.setattr(beads_meta.subprocess, "run", fake)
    monkeypatch.setattr(beads_meta.shutil, "which", lambda _name: "/usr/bin/bd")
    return fake


# ─────────────────────── list_subgraph ────────────────────────────


def test_list_subgraph_bfs_collects_descendants(fake_bd: _FakeBd) -> None:
    # root → A → B; root → C (parent-child edges going up).
    fake_bd.edges = {
        ("root", "up", "parent-child"): [
            {"id": "A", "status": "open", "title": "A"},
            {"id": "C", "status": "open", "title": "C"},
        ],
        ("root", "up", "blocks"): [],
        ("A", "up", "parent-child"): [
            {"id": "B", "status": "open", "title": "B"},
        ],
        ("A", "up", "blocks"): [],
        ("B", "up", "parent-child"): [],
        ("B", "up", "blocks"): [],
        ("C", "up", "parent-child"): [],
        ("C", "up", "blocks"): [],
        # blocks-down for each collected node (none).
        ("A", "down", "blocks"): [],
        ("B", "down", "blocks"): [],
        ("C", "down", "blocks"): [],
    }
    nodes = beads_meta.list_subgraph("root", traverse="parent-child,blocks")
    ids = sorted(n["id"] for n in nodes)
    assert ids == ["A", "B", "C"]
    # Root excluded by default (include_root=False).
    assert "root" not in ids


def test_list_subgraph_skips_closed_by_default(fake_bd: _FakeBd) -> None:
    fake_bd.edges = {
        ("root", "up", "parent-child"): [
            {"id": "A", "status": "open", "title": "A"},
            {"id": "B", "status": "closed", "title": "B"},
        ],
        ("root", "up", "blocks"): [],
        ("A", "up", "parent-child"): [],
        ("A", "up", "blocks"): [],
        ("B", "up", "parent-child"): [],
        ("B", "up", "blocks"): [],
        ("A", "down", "blocks"): [],
        ("B", "down", "blocks"): [],
    }
    nodes = beads_meta.list_subgraph("root")
    assert [n["id"] for n in nodes] == ["A"]

    nodes_with_closed = beads_meta.list_subgraph("root", include_closed=True)
    assert sorted(n["id"] for n in nodes_with_closed) == ["A", "B"]


def test_list_subgraph_include_root(fake_bd: _FakeBd) -> None:
    fake_bd.edges = {
        ("root", "up", "parent-child"): [
            {"id": "A", "status": "open", "title": "A"},
        ],
        ("root", "up", "blocks"): [],
        ("A", "up", "parent-child"): [],
        ("A", "up", "blocks"): [],
        ("root", "down", "blocks"): [],
        ("A", "down", "blocks"): [],
    }
    fake_bd.shows = {
        "root": {"id": "root", "status": "open", "title": "root bead"},
    }
    nodes = beads_meta.list_subgraph("root", include_root=True)
    assert sorted(n["id"] for n in nodes) == ["A", "root"]


def test_list_subgraph_edge_type_filtering(fake_bd: _FakeBd) -> None:
    # Only `blocks` edges; parent-child should not be queried.
    fake_bd.edges = {
        ("root", "up", "blocks"): [
            {"id": "A", "status": "open", "title": "A"},
        ],
        ("A", "up", "blocks"): [],
        ("A", "down", "blocks"): [],
    }
    nodes = beads_meta.list_subgraph("root", traverse="blocks")
    assert [n["id"] for n in nodes] == ["A"]
    # No --type=parent-child shellouts.
    types_used = {
        cmd[cmd.index("--type") + 1]
        for cmd in fake_bd.calls
        if cmd[:3] == ["bd", "dep", "list"] and "--type" in cmd
    }
    assert types_used == {"blocks"}


def test_list_subgraph_invalid_edge_type_raises() -> None:
    with pytest.raises(ValueError, match="unknown edge type"):
        beads_meta._normalize_traverse("bogus,blocks")


def test_list_subgraph_block_deps_constrained_to_set(fake_bd: _FakeBd) -> None:
    # A blocks B (both in set); X blocks A (X is closed → out of set).
    fake_bd.edges = {
        ("root", "up", "parent-child"): [
            {"id": "A", "status": "open", "title": "A"},
            {"id": "B", "status": "open", "title": "B"},
        ],
        ("root", "up", "blocks"): [],
        ("A", "up", "parent-child"): [],
        ("A", "up", "blocks"): [],
        ("B", "up", "parent-child"): [],
        ("B", "up", "blocks"): [],
        ("A", "down", "blocks"): [
            {"id": "X", "status": "closed", "title": "X"},  # out of set
        ],
        ("B", "down", "blocks"): [
            {"id": "A", "status": "open", "title": "A"},  # in set
        ],
    }
    nodes = {n["id"]: n for n in beads_meta.list_subgraph("root")}
    assert nodes["A"]["block_deps"] == []
    assert nodes["B"]["block_deps"] == ["A"]


# ─────────────────────── topo_sort_blocks ────────────────────────────


def test_topo_sort_blocks_orders_by_block_deps() -> None:
    nodes = [
        {"id": "C", "status": "open", "block_deps": ["A", "B"]},
        {"id": "B", "status": "open", "block_deps": ["A"]},
        {"id": "A", "status": "open", "block_deps": []},
    ]
    ordered = beads_meta.topo_sort_blocks(nodes)
    ids = [n["id"] for n in ordered]
    assert ids.index("A") < ids.index("B") < ids.index("C")


def test_topo_sort_blocks_cycle_raises_with_member_list() -> None:
    nodes = [
        {"id": "X", "status": "open", "block_deps": ["Y"]},
        {"id": "Y", "status": "open", "block_deps": ["X"]},
    ]
    with pytest.raises(ValueError) as exc_info:
        beads_meta.topo_sort_blocks(nodes)
    msg = str(exc_info.value)
    assert msg.startswith("dependency cycle: [")
    assert "X" in msg and "Y" in msg


def test_topo_sort_blocks_empty_input_returns_empty() -> None:
    assert beads_meta.topo_sort_blocks([]) == []
