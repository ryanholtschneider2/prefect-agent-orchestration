"""Unit tests for `beads_meta.resolve_seed_bead`.

The seed-bead walker underpins role-session affinity (prefect-orchestration-7vs.2):
for any issue, its **seed** is the topmost ancestor reachable via
parent-child edges. Walking is done with `bd dep list <cur>
--direction=down --type=parent-child`, which on this rig returns
*parents* (verified 2026-04-28). These tests use a fake `_bd_dep_list`
to assert the walk direction, the cycle guard, and the no-bd fallback.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from prefect_orchestration import beads_meta
from prefect_orchestration.beads_meta import resolve_seed_bead


@pytest.fixture
def fake_bd_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(beads_meta.shutil, "which", lambda _name: "/usr/bin/bd")


def _install_dep_list(
    monkeypatch: pytest.MonkeyPatch,
    edges: dict[str, list[str]],
) -> list[tuple[str, str, str | None]]:
    """Patch `_bd_dep_list` to return parents from a child→parents map.

    `edges["X"] = ["Y"]` means: when walking from X with
    direction=down + type=parent-child, return [{"id": "Y"}].
    Records each call so tests can assert the walk path.
    """
    calls: list[tuple[str, str, str | None]] = []

    def _fake(
        issue_id: str,
        direction: str,
        edge_type: str | None = None,
        rig_path: Path | str | None = None,
    ) -> list[dict[str, Any]]:
        calls.append((issue_id, direction, edge_type))
        parent_ids = edges.get(issue_id, [])
        return [{"id": pid} for pid in parent_ids]

    monkeypatch.setattr(beads_meta, "_bd_dep_list", _fake)
    return calls


def test_resolve_seed_self_when_bd_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No bd on PATH → return issue_id unchanged (FileStore path takes over)."""
    monkeypatch.setattr(beads_meta.shutil, "which", lambda _name: None)
    assert resolve_seed_bead("anything") == "anything"


def test_resolve_seed_self_when_no_parent(
    fake_bd_on_path: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parentless bead is its own seed."""
    _install_dep_list(monkeypatch, edges={})
    assert resolve_seed_bead("solo-1") == "solo-1"


def test_resolve_seed_walks_chain(
    fake_bd_on_path: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Single-parent chain C → B → A returns A."""
    calls = _install_dep_list(monkeypatch, edges={"C": ["B"], "B": ["A"]})
    assert resolve_seed_bead("C") == "A"
    # Verify the walk used direction=down, type=parent-child.
    assert calls == [
        ("C", "down", "parent-child"),
        ("B", "down", "parent-child"),
        ("A", "down", "parent-child"),
    ]


def test_resolve_seed_walks_uses_correct_direction_and_edge_type(
    fake_bd_on_path: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direction must be `down` + edge_type=`parent-child` (rig-verified)."""
    calls = _install_dep_list(monkeypatch, edges={"X": ["P"]})
    resolve_seed_bead("X")
    # First call is the only one that matters here; seed is P (no further parents).
    assert calls[0] == ("X", "down", "parent-child")


def test_resolve_seed_picks_first_sorted_parent_when_multi_parent(
    fake_bd_on_path: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Multi-parent: deterministic by sorted id."""
    _install_dep_list(monkeypatch, edges={"C": ["zeta", "alpha", "mu"]})
    assert resolve_seed_bead("C") == "alpha"


def test_resolve_seed_raises_on_cycle(
    fake_bd_on_path: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A → B → A is a cycle; raise rather than spin."""
    _install_dep_list(monkeypatch, edges={"A": ["B"], "B": ["A"]})
    with pytest.raises(ValueError, match="cycle"):
        resolve_seed_bead("A")


def test_resolve_seed_raises_when_chain_exceeds_max_hops(
    fake_bd_on_path: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absurd-depth chain caps out and raises."""
    # Chain of 20 ancestors, each pointing at the next, well past max_hops=3.
    edges = {f"n{i}": [f"n{i + 1}"] for i in range(20)}
    _install_dep_list(monkeypatch, edges=edges)
    with pytest.raises(ValueError, match="exceeds 3 hops"):
        resolve_seed_bead("n0", max_hops=3)


def test_resolve_seed_threads_rig_path(
    fake_bd_on_path: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`rig_path` flows through to the underlying `_bd_dep_list` shellouts."""
    seen: list[Path | str | None] = []

    def _fake(
        issue_id: str,
        direction: str,
        edge_type: str | None = None,
        rig_path: Path | str | None = None,
    ) -> list[dict[str, Any]]:
        seen.append(rig_path)
        return []  # parentless → self-seed

    monkeypatch.setattr(beads_meta, "_bd_dep_list", _fake)
    resolve_seed_bead("solo", rig_path=tmp_path)
    assert seen == [tmp_path]
