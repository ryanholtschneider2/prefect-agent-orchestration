"""Unit tests for `beads_meta.list_epic_children` discovery modes
(prefect-orchestration-h5s).

The new `mode={"ids","deps","both"}` parameter selects whether children
are discovered via the dot-suffix probe (gas-city convention), the
`bd dep` graph (parent-child + blocks edges), or a stable union of both.

These mock `subprocess.run` to feed canned `bd show` / `bd dep list`
JSON; no real `bd` invocation.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from prefect_orchestration import beads_meta


class _FakeBd:
    """Records `bd` shellouts, returns canned JSON.

    `dot_suffix_rows` keys are integer suffixes (1, 2, 3, …); a missing
    key returns the empty `bd show` shape (404). `edges` keys are
    `(issue_id, direction, edge_type)`. `shows` is for arbitrary
    `bd show <id>` lookups outside the dot-suffix probe.
    """

    def __init__(
        self,
        *,
        dot_suffix_rows: dict[int, dict] | None = None,
        edges: dict[tuple[str, str, str | None], list[dict]] | None = None,
        shows: dict[str, dict] | None = None,
        epic_id: str = "ep",
    ) -> None:
        self.dot_suffix_rows = dot_suffix_rows or {}
        self.edges = edges or {}
        self.shows = shows or {}
        self.epic_id = epic_id
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **_: Any) -> subprocess.CompletedProcess:
        self.calls.append(list(cmd))
        if cmd[:2] == ["bd", "show"]:
            issue_id = cmd[2]
            # Dot-suffix probe?
            if issue_id.startswith(f"{self.epic_id}."):
                try:
                    n = int(issue_id.rsplit(".", 1)[1])
                except ValueError:
                    n = -1
                row = self.dot_suffix_rows.get(n)
                if row is None:
                    # Mirror real `bd show <missing>` — exit 1 + empty stdout
                    # so `_dot_suffix_children` increments consecutive_missing.
                    return subprocess.CompletedProcess(
                        cmd, 1, stdout="", stderr="not found"
                    )
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps([row]), stderr=""
                )
            # Plain `bd show` (used by list_subgraph include_root + collect_explicit).
            row = self.shows.get(issue_id)
            if row is None:
                return subprocess.CompletedProcess(
                    cmd, 1, stdout="", stderr="not found"
                )
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps([row]), stderr=""
            )
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
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


@pytest.fixture
def fake_bd(monkeypatch: pytest.MonkeyPatch) -> _FakeBd:
    fake = _FakeBd()
    monkeypatch.setattr(beads_meta.subprocess, "run", fake)
    monkeypatch.setattr(beads_meta.shutil, "which", lambda _name: "/usr/bin/bd")
    return fake


# ─────────────────────── mode="ids" ────────────────────────────


def test_mode_ids_uses_dot_suffix_only(fake_bd: _FakeBd) -> None:
    fake_bd.dot_suffix_rows = {
        1: {"id": "ep.1", "status": "open", "title": "first", "dependencies": []},
        2: {
            "id": "ep.2",
            "status": "open",
            "title": "second",
            "dependencies": ["ep.1"],
        },
    }
    nodes = beads_meta.list_epic_children("ep", mode="ids")
    assert [n["id"] for n in nodes] == ["ep.1", "ep.2"]
    by_id = {n["id"]: n for n in nodes}
    assert by_id["ep.1"]["block_deps"] == []
    assert by_id["ep.2"]["block_deps"] == ["ep.1"]
    # No `bd dep list` calls — pure dot-suffix.
    assert all(cmd[:3] != ["bd", "dep", "list"] for cmd in fake_bd.calls)


def test_mode_ids_drops_out_of_set_dependencies(fake_bd: _FakeBd) -> None:
    fake_bd.dot_suffix_rows = {
        1: {
            "id": "ep.1",
            "status": "open",
            "title": "x",
            "dependencies": ["unrelated", "ep.0"],  # neither in set
        },
    }
    nodes = beads_meta.list_epic_children("ep", mode="ids")
    assert [n["id"] for n in nodes] == ["ep.1"]
    assert nodes[0]["block_deps"] == []


def test_mode_ids_default_argument_is_ids(fake_bd: _FakeBd) -> None:
    """Back-compat: calling without `mode=` still uses dot-suffix probe."""
    fake_bd.dot_suffix_rows = {
        1: {"id": "ep.1", "status": "open", "title": "x", "dependencies": []},
    }
    nodes = beads_meta.list_epic_children("ep")
    assert [n["id"] for n in nodes] == ["ep.1"]


def test_mode_ids_skips_closed_children(fake_bd: _FakeBd) -> None:
    fake_bd.dot_suffix_rows = {
        1: {"id": "ep.1", "status": "closed", "title": "done", "dependencies": []},
        2: {"id": "ep.2", "status": "open", "title": "x", "dependencies": []},
    }
    nodes = beads_meta.list_epic_children("ep", mode="ids")
    assert [n["id"] for n in nodes] == ["ep.2"]


# ─────────────────────── mode="deps" ────────────────────────────


def test_mode_deps_uses_subgraph(fake_bd: _FakeBd) -> None:
    fake_bd.edges = {
        ("ep", "up", "parent-child"): [
            {"id": "child-A", "status": "open", "title": "A"},
            {"id": "child-B", "status": "open", "title": "B"},
        ],
        ("ep", "up", "blocks"): [],
        ("child-A", "up", "parent-child"): [],
        ("child-A", "up", "blocks"): [],
        ("child-B", "up", "parent-child"): [],
        ("child-B", "up", "blocks"): [],
        ("child-A", "down", "blocks"): [],
        ("child-B", "down", "blocks"): [
            {"id": "child-A", "status": "open", "title": "A"},  # in set
        ],
    }
    nodes = beads_meta.list_epic_children("ep", mode="deps")
    by_id = {n["id"]: n for n in nodes}
    assert sorted(by_id) == ["child-A", "child-B"]
    assert by_id["child-A"]["block_deps"] == []
    assert by_id["child-B"]["block_deps"] == ["child-A"]
    # No dot-suffix probes — pure deps.
    assert all(
        not (cmd[:2] == ["bd", "show"] and cmd[2].startswith("ep."))
        for cmd in fake_bd.calls
    )


def test_mode_deps_returns_empty_when_no_edges(fake_bd: _FakeBd) -> None:
    fake_bd.edges = {
        ("ep", "up", "parent-child"): [],
        ("ep", "up", "blocks"): [],
    }
    assert beads_meta.list_epic_children("ep", mode="deps") == []


# ─────────────────────── mode="both" ────────────────────────────


def test_mode_both_unions_dedups(fake_bd: _FakeBd) -> None:
    # bd-dep edges expose A + B; dot-suffix probe finds B + C.
    # Expected: A first (deps order), then B (already in set, no dup),
    # then C (ids-only).
    fake_bd.edges = {
        ("ep", "up", "parent-child"): [
            {"id": "A", "status": "open", "title": "A"},
            {"id": "B", "status": "open", "title": "B"},
        ],
        ("ep", "up", "blocks"): [],
        ("A", "up", "parent-child"): [],
        ("A", "up", "blocks"): [],
        ("B", "up", "parent-child"): [],
        ("B", "up", "blocks"): [],
        ("A", "down", "blocks"): [],
        ("B", "down", "blocks"): [
            {"id": "A", "status": "open", "title": "A"},
        ],
    }
    fake_bd.dot_suffix_rows = {
        # ep.1 == B (already discovered via deps), ep.2 == C (ids-only)
        1: {"id": "B", "status": "open", "title": "B", "dependencies": []},
        2: {"id": "C", "status": "open", "title": "C", "dependencies": ["B"]},
    }
    nodes = beads_meta.list_epic_children("ep", mode="both")
    ids = [n["id"] for n in nodes]
    # Deps order: A, B; then ids-only: C.
    assert ids == ["A", "B", "C"]
    by_id = {n["id"]: n for n in nodes}
    # B's block_deps come from the deps walker (A); ids walker said
    # nothing extra so the set is unchanged.
    assert by_id["B"]["block_deps"] == ["A"]
    # C only came from ids; its raw `dependencies: ["B"]` survives because
    # B is in the merged in-set.
    assert by_id["C"]["block_deps"] == ["B"]


def test_mode_both_falls_back_to_ids_when_no_deps(fake_bd: _FakeBd) -> None:
    fake_bd.edges = {
        ("ep", "up", "parent-child"): [],
        ("ep", "up", "blocks"): [],
    }
    fake_bd.dot_suffix_rows = {
        1: {"id": "ep.1", "status": "open", "title": "x", "dependencies": []},
    }
    nodes = beads_meta.list_epic_children("ep", mode="both")
    assert [n["id"] for n in nodes] == ["ep.1"]


def test_mode_both_falls_back_to_deps_when_no_ids(fake_bd: _FakeBd) -> None:
    fake_bd.edges = {
        ("ep", "up", "parent-child"): [
            {"id": "child-A", "status": "open", "title": "A"},
        ],
        ("ep", "up", "blocks"): [],
        ("child-A", "up", "parent-child"): [],
        ("child-A", "up", "blocks"): [],
        ("child-A", "down", "blocks"): [],
    }
    nodes = beads_meta.list_epic_children("ep", mode="both")
    assert [n["id"] for n in nodes] == ["child-A"]


# ─────────────────────── validation ────────────────────────────


def test_invalid_mode_raises() -> None:
    with pytest.raises(ValueError, match="unknown discover mode"):
        beads_meta.list_epic_children("ep", mode="bogus")  # type: ignore[arg-type]


# ─────────────────────── collect_explicit_children ────────────────────────────


def test_collect_explicit_children_happy_path(fake_bd: _FakeBd) -> None:
    fake_bd.shows = {
        "x1": {"id": "x1", "status": "open", "title": "first"},
        "x2": {"id": "x2", "status": "open", "title": "second"},
        "x3": {"id": "x3", "status": "open", "title": "third"},
    }
    fake_bd.edges = {
        ("x1", "down", "blocks"): [],
        ("x2", "down", "blocks"): [
            {"id": "x1", "status": "open", "title": "first"},
        ],
        ("x3", "down", "blocks"): [
            {"id": "x2", "status": "open", "title": "second"},
            {"id": "out-of-set", "status": "open", "title": "?"},  # dropped
        ],
    }
    nodes = beads_meta.collect_explicit_children(["x1", "x2", "x3"])
    assert [n["id"] for n in nodes] == ["x1", "x2", "x3"]
    by_id = {n["id"]: n for n in nodes}
    assert by_id["x1"]["block_deps"] == []
    assert by_id["x2"]["block_deps"] == ["x1"]
    # Out-of-set dep dropped.
    assert by_id["x3"]["block_deps"] == ["x2"]


def test_collect_explicit_children_missing_id_raises(fake_bd: _FakeBd) -> None:
    fake_bd.shows = {"a": {"id": "a", "status": "open", "title": "a"}}
    with pytest.raises(ValueError, match="unknown child id"):
        beads_meta.collect_explicit_children(["a", "ghost"])


def test_collect_explicit_children_closed_id_raises(fake_bd: _FakeBd) -> None:
    fake_bd.shows = {
        "a": {"id": "a", "status": "open", "title": "a"},
        "b": {"id": "b", "status": "closed", "title": "b"},
    }
    with pytest.raises(ValueError, match="closed child id"):
        beads_meta.collect_explicit_children(["a", "b"])


def test_collect_explicit_children_duplicate_raises() -> None:
    with pytest.raises(ValueError, match="duplicate child id"):
        beads_meta.collect_explicit_children(["a", "b", "a"])


def test_collect_explicit_children_empty_raises() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        beads_meta.collect_explicit_children([])
    with pytest.raises(ValueError, match="non-empty"):
        beads_meta.collect_explicit_children(["", "  "])
