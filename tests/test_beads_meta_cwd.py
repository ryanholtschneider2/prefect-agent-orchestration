"""Unit tests for cwd propagation + list-vs-dict shape coercion in
`beads_meta`.

Backs prefect-orchestration-3mw. Every bd shellout in core must pass
`cwd=str(rig_path)` when the caller supplied a rig_path, so beads
resolves the rig's `.beads/` and not the Python process cwd. Without
this, Prefect task runners (which inherit an unpredictable cwd) hit
the wrong database.

These tests mock `subprocess.run` and assert the recorded `cwd` kwarg.
No real `bd` invocation.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from prefect_orchestration import beads_meta
from prefect_orchestration.beads_meta import (
    BeadsStore,
    auto_store,
    claim_issue,
    close_issue,
    collect_explicit_children,
    list_epic_children,
    list_subgraph,
)


# ─────────────────────── fakes ────────────────────────────


class _FakeBd:
    """Records `(cmd, cwd)` for every shellout; returns canned bd output.

    `responses` keys: tuple of cmd[0:N] (variable depth, see lookup
    logic below). Default is exit 0 with empty stdout.
    """

    def __init__(
        self,
        shows: dict[str, Any] | None = None,
        deps: dict[tuple[str, str, str | None], list[dict]] | None = None,
    ) -> None:
        self.shows = shows or {}
        self.deps = deps or {}
        self.calls: list[tuple[list[str], Any]] = []

    def __call__(self, cmd: list[str], **kw: Any) -> subprocess.CompletedProcess:
        self.calls.append((list(cmd), kw.get("cwd")))
        if cmd[:2] == ["bd", "show"]:
            issue = cmd[2]
            payload = self.shows.get(issue, None)
            if payload is None:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(payload), stderr=""
            )
        if cmd[:3] == ["bd", "dep", "list"]:
            issue = cmd[3]
            direction = next(
                (a.split("=", 1)[1] for a in cmd if a.startswith("--direction=")),
                "down",
            )
            edge_type: str | None = None
            if "--type" in cmd:
                edge_type = cmd[cmd.index("--type") + 1]
            rows = self.deps.get((issue, direction, edge_type), [])
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(rows), stderr=""
            )
        # bd update / close / others — exit 0, empty.
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


@pytest.fixture
def fake_bd(monkeypatch: pytest.MonkeyPatch) -> _FakeBd:
    fake = _FakeBd()
    monkeypatch.setattr(beads_meta.subprocess, "run", fake)
    monkeypatch.setattr(beads_meta.shutil, "which", lambda _name: "/usr/bin/bd")
    return fake


# ─────────────────────── BeadsStore: cwd ────────────────────────────


def test_beadsstore_get_passes_cwd_when_rig_path_set(
    fake_bd: _FakeBd, tmp_path: Path
) -> None:
    fake_bd.shows = {"E": {"id": "E", "metadata": {"k": "v"}}}
    store = BeadsStore(parent_id="E", rig_path=tmp_path)
    assert store.get("k") == "v"
    assert any(
        cmd[:3] == ["bd", "show", "E"] and cwd == str(tmp_path)
        for cmd, cwd in fake_bd.calls
    )


def test_beadsstore_get_defaults_to_no_cwd_when_rig_path_none(
    fake_bd: _FakeBd,
) -> None:
    fake_bd.shows = {"E": {"id": "E", "metadata": {"k": "v"}}}
    store = BeadsStore(parent_id="E")
    assert store.get("k") == "v"
    cwds = [cwd for cmd, cwd in fake_bd.calls if cmd[:3] == ["bd", "show", "E"]]
    # When rig_path is None, cwd is passed as None (i.e. inherits process cwd).
    assert cwds == [None]


def test_beadsstore_set_passes_cwd(fake_bd: _FakeBd, tmp_path: Path) -> None:
    BeadsStore(parent_id="E", rig_path=tmp_path).set("k", "v")
    assert any(
        cmd[:3] == ["bd", "update", "E"] and cwd == str(tmp_path)
        for cmd, cwd in fake_bd.calls
    )


def test_beadsstore_all_passes_cwd(fake_bd: _FakeBd, tmp_path: Path) -> None:
    fake_bd.shows = {"E": {"id": "E", "metadata": {"a": "1", "b": "2"}}}
    assert BeadsStore(parent_id="E", rig_path=tmp_path).all() == {"a": "1", "b": "2"}
    assert any(
        cmd[:3] == ["bd", "show", "E"] and cwd == str(tmp_path)
        for cmd, cwd in fake_bd.calls
    )


# ─────────────────────── BeadsStore: list-vs-dict shape ──────────────


def test_beadsstore_get_handles_list_shape(
    fake_bd: _FakeBd, tmp_path: Path
) -> None:
    """Some bd versions return a single-row JSON list, not a dict."""
    fake_bd.shows = {"E": [{"id": "E", "metadata": {"k": "v"}}]}
    assert BeadsStore(parent_id="E", rig_path=tmp_path).get("k") == "v"


def test_beadsstore_get_handles_dict_shape(
    fake_bd: _FakeBd, tmp_path: Path
) -> None:
    """Other bd versions return a bare dict."""
    fake_bd.shows = {"E": {"id": "E", "metadata": {"k": "v"}}}
    assert BeadsStore(parent_id="E", rig_path=tmp_path).get("k") == "v"


def test_beadsstore_all_handles_list_shape(
    fake_bd: _FakeBd, tmp_path: Path
) -> None:
    fake_bd.shows = {"E": [{"id": "E", "metadata": {"a": "1"}}]}
    assert BeadsStore(parent_id="E", rig_path=tmp_path).all() == {"a": "1"}


def test_beadsstore_all_handles_dict_shape(
    fake_bd: _FakeBd, tmp_path: Path
) -> None:
    fake_bd.shows = {"E": {"id": "E", "metadata": {"a": "1"}}}
    assert BeadsStore(parent_id="E", rig_path=tmp_path).all() == {"a": "1"}


def test_beadsstore_get_handles_empty_list(
    fake_bd: _FakeBd, tmp_path: Path
) -> None:
    """Empty list (bd show emitted []) returns the default sentinel."""
    fake_bd.shows = {"E": []}
    assert BeadsStore(parent_id="E", rig_path=tmp_path).get("k", "fallback") == "fallback"


def test_beadsstore_get_handles_missing_metadata_key(
    fake_bd: _FakeBd, tmp_path: Path
) -> None:
    """Row exists but has no `metadata` field → return default."""
    fake_bd.shows = {"E": {"id": "E"}}
    assert BeadsStore(parent_id="E", rig_path=tmp_path).get("k", None) is None


# ─────────────────────── claim_issue / close_issue ───────────────────


def test_claim_issue_passes_cwd(fake_bd: _FakeBd, tmp_path: Path) -> None:
    claim_issue("E-1", "po-abc", rig_path=tmp_path)
    assert any(
        cmd[:3] == ["bd", "update", "E-1"]
        and "--status" in cmd
        and cwd == str(tmp_path)
        for cmd, cwd in fake_bd.calls
    )


def test_claim_issue_no_cwd_when_rig_path_none(fake_bd: _FakeBd) -> None:
    claim_issue("E-1", "po-abc")
    cwds = [cwd for cmd, cwd in fake_bd.calls if cmd[:2] == ["bd", "update"]]
    assert cwds == [None]


def test_close_issue_passes_cwd(fake_bd: _FakeBd, tmp_path: Path) -> None:
    close_issue("E-1", notes="done", rig_path=tmp_path)
    assert any(
        cmd[:3] == ["bd", "close", "E-1"] and cwd == str(tmp_path)
        for cmd, cwd in fake_bd.calls
    )


def test_close_issue_no_cwd_when_rig_path_none(fake_bd: _FakeBd) -> None:
    close_issue("E-1")
    cwds = [cwd for cmd, cwd in fake_bd.calls if cmd[:2] == ["bd", "close"]]
    assert cwds == [None]


# ─────────────────────── _bd_show / _bd_dep_list ─────────────────────


def test_bd_show_passes_cwd(fake_bd: _FakeBd, tmp_path: Path) -> None:
    fake_bd.shows = {"E": {"id": "E", "status": "open", "title": "x"}}
    row = beads_meta._bd_show("E", rig_path=tmp_path)
    assert row is not None and row["id"] == "E"
    assert any(
        cmd[:3] == ["bd", "show", "E"] and cwd == str(tmp_path)
        for cmd, cwd in fake_bd.calls
    )


def test_bd_show_no_cwd_when_rig_path_none(fake_bd: _FakeBd) -> None:
    fake_bd.shows = {"E": {"id": "E", "status": "open", "title": "x"}}
    beads_meta._bd_show("E")
    cwds = [cwd for cmd, cwd in fake_bd.calls if cmd[:3] == ["bd", "show", "E"]]
    assert cwds == [None]


def test_bd_dep_list_passes_cwd(fake_bd: _FakeBd, tmp_path: Path) -> None:
    fake_bd.deps = {("E", "up", "blocks"): [{"id": "C", "status": "open"}]}
    rows = beads_meta._bd_dep_list("E", direction="up", edge_type="blocks", rig_path=tmp_path)
    assert rows == [{"id": "C", "status": "open"}]
    assert any(
        cmd[:4] == ["bd", "dep", "list", "E"] and cwd == str(tmp_path)
        for cmd, cwd in fake_bd.calls
    )


# ─────────────────────── traversal API ───────────────────────────────


def test_list_subgraph_propagates_cwd(fake_bd: _FakeBd, tmp_path: Path) -> None:
    fake_bd.deps = {
        ("R", "up", "parent-child"): [{"id": "A", "status": "open", "title": "A"}],
        ("R", "up", "blocks"): [],
        ("A", "up", "parent-child"): [],
        ("A", "up", "blocks"): [],
        ("A", "down", "blocks"): [],
    }
    nodes = list_subgraph("R", traverse="parent-child,blocks", rig_path=tmp_path)
    assert [n["id"] for n in nodes] == ["A"]
    # Every bd call must carry cwd=tmp_path.
    cwds = {cwd for _cmd, cwd in fake_bd.calls if _cmd[0] == "bd"}
    assert cwds == {str(tmp_path)}


def test_list_epic_children_deps_mode_propagates_cwd(
    fake_bd: _FakeBd, tmp_path: Path
) -> None:
    fake_bd.deps = {
        ("E", "up", "parent-child"): [
            {"id": "C1", "status": "open", "title": "c1"},
            {"id": "C2", "status": "open", "title": "c2"},
        ],
        ("E", "up", "blocks"): [],
        ("C1", "up", "parent-child"): [],
        ("C1", "up", "blocks"): [],
        ("C2", "up", "parent-child"): [],
        ("C2", "up", "blocks"): [],
        ("C1", "down", "blocks"): [],
        ("C2", "down", "blocks"): [],
    }
    nodes = list_epic_children("E", mode="deps", rig_path=tmp_path)
    assert sorted(n["id"] for n in nodes) == ["C1", "C2"]
    cwds = {cwd for cmd, cwd in fake_bd.calls if cmd[0] == "bd"}
    assert cwds == {str(tmp_path)}


def test_list_epic_children_ids_mode_propagates_cwd(
    fake_bd: _FakeBd, tmp_path: Path
) -> None:
    fake_bd.shows = {
        "E.1": {"id": "E.1", "status": "open", "title": "one"},
        # E.2, E.3, E.4 → 3 consecutive misses → probe stops.
    }
    nodes = list_epic_children("E", mode="ids", rig_path=tmp_path)
    assert [n["id"] for n in nodes] == ["E.1"]
    cwds = {cwd for cmd, cwd in fake_bd.calls if cmd[0] == "bd"}
    assert cwds == {str(tmp_path)}


def test_collect_explicit_children_propagates_cwd(
    fake_bd: _FakeBd, tmp_path: Path
) -> None:
    fake_bd.shows = {
        "X": {"id": "X", "status": "open", "title": "x"},
        "Y": {"id": "Y", "status": "open", "title": "y"},
    }
    fake_bd.deps = {
        ("X", "down", "blocks"): [],
        ("Y", "down", "blocks"): [],
    }
    nodes = collect_explicit_children(["X", "Y"], rig_path=tmp_path)
    assert [n["id"] for n in nodes] == ["X", "Y"]
    cwds = {cwd for cmd, cwd in fake_bd.calls if cmd[0] == "bd"}
    assert cwds == {str(tmp_path)}


# ─────────────────────── auto_store ──────────────────────────────────


def test_auto_store_threads_rig_path_to_beads_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When `bd` is on PATH, auto_store returns a BeadsStore carrying rig_path."""
    monkeypatch.setattr(beads_meta.shutil, "which", lambda _name: "/usr/bin/bd")
    store = auto_store("E", tmp_path / "run", rig_path=tmp_path)
    assert isinstance(store, BeadsStore)
    assert store.parent_id == "E"
    assert store.rig_path == tmp_path


def test_auto_store_falls_back_to_filestore_when_bd_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(beads_meta.shutil, "which", lambda _name: None)
    store = auto_store("E", tmp_path, rig_path=tmp_path)
    # FileStore — cwd-independent, no rig_path attribute needed.
    assert not isinstance(store, BeadsStore)
