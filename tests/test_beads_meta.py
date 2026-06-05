"""Unit tests for `beads_meta.resolve_seed_bead`.

The seed-bead walker underpins role-session affinity (prefect-orchestration-7vs.2):
for any issue, its **seed** is the topmost ancestor reachable via
parent-child edges. Walking is done with `bd dep list <cur>
--direction=down --type=parent-child`, which on this rig returns
*parents* (verified 2026-04-28). These tests use a fake `_bd_dep_list`
to assert the walk direction, the cycle guard, and the no-bd fallback.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from prefect_orchestration import beads_meta
from prefect_orchestration.beads_meta import (
    iter_bead_id,
    iter_bead_re,
    resolve_seed_bead,
)


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


# ─── create_child_bead `blocks` kwarg (prefect-orchestration-7vs.4) ───


def test_create_child_bead_forwards_blocks_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`blocks="prev-id"` appends `--deps blocks:prev-id` to the bd command."""
    monkeypatch.delenv("PO_BEADS_BACKEND", raising=False)
    monkeypatch.setattr(beads_meta.shutil, "which", lambda _name: f"/usr/bin/{_name}")
    captured: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, *a, **kw):  # type: ignore[no-untyped-def]
        captured.append(list(cmd))
        return _Proc()

    monkeypatch.setattr(beads_meta.subprocess, "run", _fake_run)
    out = beads_meta.create_child_bead(
        "parent",
        "parent-iter2",
        title="t",
        description="d",
        rig_path=tmp_path,
        blocks="parent-iter1",
    )
    assert out == "parent-iter2"
    assert captured, "no shellout recorded"
    cmd = captured[0]
    assert "--parent" not in cmd
    assert "--deps" in cmd
    deps_idx = cmd.index("--deps")
    assert cmd[deps_idx + 1] == "parent-child:parent,blocks:parent-iter1"


def test_create_child_bead_emits_parent_child_dep_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No `blocks` kwarg → `--deps parent-child:<parent>` only.

    bd 1.0 rejects `--id` + `--parent` together, so the parent edge
    is always expressed via `--deps`, never as a separate `--parent` flag.
    """
    monkeypatch.delenv("PO_BEADS_BACKEND", raising=False)
    monkeypatch.setattr(beads_meta.shutil, "which", lambda _name: f"/usr/bin/{_name}")
    captured: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, *a, **kw):  # type: ignore[no-untyped-def]
        captured.append(list(cmd))
        return _Proc()

    monkeypatch.setattr(beads_meta.subprocess, "run", _fake_run)
    beads_meta.create_child_bead(
        "parent",
        "parent.lint.1",
        title="t",
        description="d",
        rig_path=tmp_path,
    )
    cmd = captured[0]
    assert "--parent" not in cmd
    assert "--deps" in cmd
    deps_idx = cmd.index("--deps")
    assert cmd[deps_idx + 1] == "parent-child:parent"


# ─── read_iter_cap (prefect-orchestration-7vs.4) ───


def test_read_iter_cap_default_when_bd_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(beads_meta, "_bd_available", lambda: False)
    assert beads_meta.read_iter_cap("parent", 3) == 3


def test_read_iter_cap_default_when_key_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(beads_meta, "_bd_available", lambda: True)
    monkeypatch.setattr(
        beads_meta,
        "_bd_show",
        lambda issue_id, rig_path=None: {"id": "parent", "metadata": {}},
    )
    assert beads_meta.read_iter_cap("parent", 5) == 5


def test_read_iter_cap_parses_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(beads_meta, "_bd_available", lambda: True)
    monkeypatch.setattr(
        beads_meta,
        "_bd_show",
        lambda issue_id, rig_path=None: {
            "id": "parent",
            "metadata": {"po.iter_cap": "7"},
        },
    )
    assert beads_meta.read_iter_cap("parent", 3) == 7


def test_read_iter_cap_falls_back_on_non_int(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(beads_meta, "_bd_available", lambda: True)
    monkeypatch.setattr(
        beads_meta,
        "_bd_show",
        lambda issue_id, rig_path=None: {
            "id": "parent",
            "metadata": {"po.iter_cap": "not-an-int"},
        },
    )
    assert beads_meta.read_iter_cap("parent", 4) == 4


def test_read_iter_cap_falls_back_on_non_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(beads_meta, "_bd_available", lambda: True)
    monkeypatch.setattr(
        beads_meta,
        "_bd_show",
        lambda issue_id, rig_path=None: {
            "id": "parent",
            "metadata": {"po.iter_cap": "0"},
        },
    )
    assert beads_meta.read_iter_cap("parent", 4) == 4


def test_read_iter_cap_honors_metadata_key_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(beads_meta, "_bd_available", lambda: True)
    monkeypatch.setattr(
        beads_meta,
        "_bd_show",
        lambda issue_id, rig_path=None: {
            "id": "parent",
            "metadata": {"po.plan_iter_cap": "2"},
        },
    )
    assert beads_meta.read_iter_cap("parent", 5, metadata_key="po.plan_iter_cap") == 2


# ─── write-side backend threading (prefect-orchestration-9xa.1) ───
#
# claim_issue / close_issue / create_child_bead / mint_seed_bead resolve
# `bd` vs `br` via `_resolve_binary`. claim/close are flag-identical on
# both backends (binary swap only); create/mint diverge — br has no
# `--id`, takes the title positionally, and reports the assigned id via
# `--json`.


class _RecordingProc:
    """A CompletedProcess-shaped stub that records the cmd it was given."""

    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def _capture_run(
    monkeypatch: pytest.MonkeyPatch, stdout: str = "", returncode: int = 0
) -> list[list[str]]:
    """Patch subprocess.run to record cmds; return the capture list."""
    captured: list[list[str]] = []

    def _fake_run(cmd, *a, **kw):  # type: ignore[no-untyped-def]
        captured.append(list(cmd))
        return _RecordingProc(stdout=stdout, returncode=returncode)

    monkeypatch.setattr(beads_meta.subprocess, "run", _fake_run)
    return captured


def _force_backend(monkeypatch: pytest.MonkeyPatch, backend: str) -> None:
    monkeypatch.setenv("PO_BEADS_BACKEND", backend)
    monkeypatch.setattr(beads_meta.shutil, "which", lambda _name: f"/usr/bin/{_name}")


def test_resolve_binary_returns_none_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PO_BEADS_BACKEND", "br")
    monkeypatch.setattr(beads_meta.shutil, "which", lambda _name: None)
    assert beads_meta._resolve_binary(None) is None


def test_resolve_binary_maps_br_backend_to_br(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_backend(monkeypatch, "br")
    assert beads_meta._resolve_binary(None) == "br"


def test_claim_issue_uses_br_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_backend(monkeypatch, "br")
    captured = _capture_run(monkeypatch)
    beads_meta.claim_issue("x-1", "po-worker")
    assert captured[0] == [
        "br",
        "update",
        "x-1",
        "--status",
        "in_progress",
        "--assignee",
        "po-worker",
    ]


def test_claim_issue_uses_bd_binary_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_backend(monkeypatch, "dolt")
    captured = _capture_run(monkeypatch)
    beads_meta.claim_issue("d-1", "po-worker")
    assert captured[0][:2] == ["bd", "update"]


def test_claim_issue_noop_when_binary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(beads_meta.shutil, "which", lambda _name: None)
    captured = _capture_run(monkeypatch)
    beads_meta.claim_issue("x-1", "po-worker")
    assert captured == []


def test_close_issue_uses_br_binary_with_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_backend(monkeypatch, "br")
    captured = _capture_run(monkeypatch)
    beads_meta.close_issue("x-1", notes="complete: done")
    assert captured[0] == ["br", "close", "x-1", "--reason", "complete: done"]


def test_close_issue_uses_bd_binary_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_backend(monkeypatch, "dolt")
    captured = _capture_run(monkeypatch)
    beads_meta.close_issue("d-1")
    assert captured[0] == ["bd", "close", "d-1"]


def test_close_issue_noop_when_binary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(beads_meta.shutil, "which", lambda _name: None)
    captured = _capture_run(monkeypatch)
    beads_meta.close_issue("x-1")
    assert captured == []


def test_create_child_bead_br_omits_id_uses_positional_title_and_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_backend(monkeypatch, "br")
    captured = _capture_run(monkeypatch, stdout='{"id": "bd-9zz"}')
    out = beads_meta.create_child_bead(
        "parent",
        "parent-iter2",
        title="my title",
        description="d",
        blocks="parent-iter1",
    )
    # br assigns its own id; the function returns it, not the requested one.
    assert out == "bd-9zz"
    cmd = captured[0]
    assert cmd[0] == "br"
    assert not any(c.startswith("--id") for c in cmd)
    assert "--title" not in cmd  # br takes the title positionally
    assert "my title" in cmd
    assert "--json" in cmd
    deps_idx = cmd.index("--deps")
    assert cmd[deps_idx + 1] == "parent-child:parent,blocks:parent-iter1"


def test_create_child_bead_br_falls_back_to_requested_id_when_json_idless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_backend(monkeypatch, "br")
    _capture_run(monkeypatch, stdout="not json")
    out = beads_meta.create_child_bead(
        "parent", "parent-iter2", title="t", description="d"
    )
    assert out == "parent-iter2"


def test_create_child_bead_missing_binary_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(beads_meta.shutil, "which", lambda _name: None)
    with pytest.raises(NotImplementedError):
        beads_meta.create_child_bead("parent", "p-iter1", title="t", description="d")


def test_mint_seed_bead_br_parses_json_id_and_uses_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_backend(monkeypatch, "br")
    captured = _capture_run(monkeypatch, stdout='{"id": "bd-seed1"}')
    out = beads_meta.mint_seed_bead(
        "myrig", "Do the thing", title="Seed", label="feature"
    )
    assert out == "bd-seed1"
    cmd = captured[0]
    assert cmd[0] == "br"
    assert not any(c.startswith("--id") for c in cmd)
    assert "Seed" in cmd  # positional title
    assert "--json" in cmd
    assert "--labels" in cmd and "feature" in cmd
    assert "--label" not in cmd  # br uses --labels, not bd's --label


def test_mint_seed_bead_br_raises_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_backend(monkeypatch, "br")
    _capture_run(monkeypatch, stdout="boom", returncode=1)
    with pytest.raises(RuntimeError):
        beads_meta.mint_seed_bead("myrig", "Do the thing")


def test_parse_created_id_tolerates_log_lines() -> None:
    body = 'INFO some log\n{"id": "bd-1ab", "title": "x"}\n'
    assert beads_meta._parse_created_id(body) == "bd-1ab"
    assert beads_meta._parse_created_id("") is None
    assert beads_meta._parse_created_id("no json here") is None


# ─── real-br write-side round-trip (gated on the br CLI) ───


@pytest.mark.skipif(shutil.which("br") is None, reason="br CLI not installed")
def test_real_br_write_side_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Genuine br workspace: mint → create child → claim → close, all via
    the write-side helpers, asserting state through `br show`."""
    import subprocess as _sp

    monkeypatch.delenv("PO_BEADS_BACKEND", raising=False)
    _sp.run(["br", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    assert beads_meta.resolve_backend(tmp_path) == "br"

    seed = beads_meta.mint_seed_bead(
        "round", "Seed task", rig_path=tmp_path, label="feature"
    )
    assert seed  # a real br id was parsed back

    child = beads_meta.create_child_bead(
        seed, "round-iter1", title="iter 1", description="d", rig_path=tmp_path
    )
    assert child and child != "round-iter1"  # br minted its own id

    beads_meta.claim_issue(child, "po-worker", rig_path=tmp_path)
    row = beads_meta._bd_show(child, rig_path=tmp_path)
    assert row is not None
    assert row["status"] == "in_progress"
    assert row.get("assignee") == "po-worker"

    beads_meta.close_issue(child, notes="complete: done", rig_path=tmp_path)
    row = beads_meta._bd_show(child, rig_path=tmp_path)
    assert row is not None and row["status"] == "closed"


# ─── iter-bead id helpers (br-ready hyphen convention) ──────────────────


def test_iter_bead_id_hyphenated() -> None:
    """Iter ids use hyphens (br rejects dots) — `<seed>-<step>-iter<N>`."""
    assert iter_bead_id("courtpro-0qt", "ralph", 1) == "courtpro-0qt-ralph-iter1"
    assert iter_bead_id("seed", "build", 3) == "seed-build-iter3"


def test_iter_bead_id_hyphenated_step() -> None:
    """A hyphen-bearing step (`plan-critic`) survives round-trip parsing."""
    bid = iter_bead_id("prefect-orchestration-5w3", "plan-critic", 2)
    assert bid == "prefect-orchestration-5w3-plan-critic-iter2"
    m = iter_bead_re("prefect-orchestration-5w3").match(bid)
    assert m is not None
    assert m.group(1) == "plan-critic"
    assert m.group(2) == "2"


def test_iter_bead_re_round_trips_id() -> None:
    """`iter_bead_re` captures (step, iter_n) for ids from `iter_bead_id`."""
    seed = "iss-1"
    pat = iter_bead_re(seed)
    m = pat.match(iter_bead_id(seed, "triage", 1))
    assert m is not None and (m.group(1), m.group(2)) == ("triage", "1")


def test_iter_bead_re_rejects_foreign_seed_and_dotted_legacy() -> None:
    """The matcher is seed-anchored and rejects the legacy dotted form."""
    pat = iter_bead_re("iss-1")
    assert pat.match("other-2-build-iter1") is None  # different seed
    assert pat.match("iss-1.build.iter1") is None  # legacy dotted id
    assert pat.match("iss-1") is None  # the seed itself is not an iter bead
