"""E2E regression for prefect-orchestration-3mw.

Provisions a temp `tmp_path` rig with `bd init`, creates an epic + 3
parent-child children, then invokes `epic_run.fn` from a Python cwd
that is **not** the rig. Without the 3mw fix, the bd shellouts
inherit the test's cwd and either resolve the wrong `.beads/` or fail
outright; with the fix, every shellout runs against `rig_path` and
discovery returns all 3 children.

This test uses the **real** `bd` binary against a real (temp) Dolt
database — that is what makes it an e2e rather than a unit test. The
Prefect engine and the per-child `software_dev_full` actor-critic
loop are mocked out: `epic_run.fn` is invoked directly (no flow
context needed) and `_dispatch_nodes` is replaced with a capture stub.
This isolates the test to the *cwd-plumbing* aspect of 3mw — the
downstream formula is exercised by other tests / runs.

Skipped when `bd` is not on PATH or when `dolt` (bd's storage backend)
is missing — both required for `bd init`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


pytestmark = pytest.mark.skipif(
    shutil.which("bd") is None or shutil.which("dolt") is None,
    reason="bd and dolt must be on PATH for the real-bd e2e test",
)


# ─────────────────────── bd helpers ────────────────────────────


def _bd_env() -> dict[str, str]:
    return {
        **os.environ,
        "BD_NON_INTERACTIVE": "1",
        # Avoid leaking developer-level git config into the test tree.
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }


def _bd_init(rig: Path) -> None:
    rig.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["bd", "init", "--prefix", "tepic"],
        cwd=str(rig),
        capture_output=True,
        text=True,
        env=_bd_env(),
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"bd init failed: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )


def _bd_q(rig: Path, *args: str) -> str:
    """`bd q` — quick capture; returns just the bead id on stdout."""
    proc = subprocess.run(
        ["bd", "q", *args],
        cwd=str(rig),
        capture_output=True,
        text=True,
        env=_bd_env(),
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"bd q failed: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    bid = proc.stdout.strip().splitlines()[-1].strip()
    assert bid, f"bd q produced no id; stdout={proc.stdout!r}"
    return bid


def _bd_dep_add(rig: Path, parent: str, child: str) -> None:
    proc = subprocess.run(
        ["bd", "dep", "add", child, parent, "--type", "parent-child"],
        cwd=str(rig),
        capture_output=True,
        text=True,
        env=_bd_env(),
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"bd dep add failed: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )


# ─────────────────────── stubs for epic_run.fn ────────────────────────


class _NullLogger:
    def info(self, *_a: Any, **_k: Any) -> None: ...
    def warning(self, *_a: Any, **_k: Any) -> None: ...
    def error(self, *_a: Any, **_k: Any) -> None: ...


# ─────────────────────── the test ─────────────────────────────────────


def test_epic_run_dispatches_three_children_from_outside_rig_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backs prefect-orchestration-3mw AC5 + AC6.

    With the cwd-plumbing fix:

    - `epic_run.fn` is invoked from a cwd OUTSIDE `rig`. Without the
      fix, the bd shellouts under `list_epic_children(mode="deps")`
      inherit this cwd and resolve a different (or non-existent)
      `.beads/` — discovery returns 0 children and the dispatch is
      empty. With the fix, all 3 children are discovered.
    - `_dispatch_nodes` is mocked to capture the nodes that *would* be
      submitted; the per-child `software_dev_full` actor-critic loop
      is NOT exercised. That keeps the test fast and focused on the
      cwd-plumbing surface (the meat of 3mw's repro).
    """
    rig = tmp_path / "rig"
    _bd_init(rig)

    # Create the epic + 3 children. Children are tasks so they show up
    # as ready work after the parent-child dep edge is added.
    epic_id = _bd_q(rig, "Epic for 3mw e2e", "--type", "epic")
    child_ids = [_bd_q(rig, f"child-{i}", "--type", "task") for i in range(1, 4)]
    for child in child_ids:
        _bd_dep_add(rig, epic_id, child)

    # Run from a cwd that is NOT the rig. This is the bug repro: prior
    # to 3mw, bd shellouts inherit this cwd and target the wrong .beads/.
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)

    captured: list[dict[str, Any]] = []

    def fake_dispatch(
        *,
        nodes: list[dict[str, Any]],
        rig: str,  # noqa: ARG001
        rig_path: str,  # noqa: ARG001
        formula_callable: Any,  # noqa: ARG001
        parent_bead: str | None,  # noqa: ARG001
        iter_caps: dict[str, Any],  # noqa: ARG001
        dry_run: bool,  # noqa: ARG001
        max_issues: int | None,  # noqa: ARG001
        logger: Any,  # noqa: ARG001
    ) -> dict[str, Any]:
        captured.extend(nodes)
        return {
            "submitted": len(nodes),
            "results": {n["id"]: {"status": "ok"} for n in nodes},
        }

    from po_formulas import epic as epic_mod

    with (
        patch("po_formulas.epic.get_run_logger", return_value=_NullLogger()),
        patch("po_formulas.epic._tag_root_run", return_value=None),
        patch("po_formulas.epic._dispatch_nodes", side_effect=fake_dispatch),
        patch(
            "po_formulas.epic._resolve_formula",
            return_value=lambda **_kw: {"status": "ok"},
        ),
        patch("po_formulas.epic._check_formula_signature", return_value=None),
    ):
        result = epic_mod.epic_run.fn(
            epic_id=epic_id,
            rig="rig",
            rig_path=str(rig),
            dry_run=True,
            discover="deps",
        )

    # Discovery must have walked the rig's `.beads/` (cwd plumbing) and
    # surfaced all 3 children. Pre-3mw, this would be 0 because the
    # shellouts inherited `outside` cwd.
    captured_ids = sorted(n["id"] for n in captured)
    assert captured_ids == sorted(child_ids), (
        f"expected {sorted(child_ids)} captured, got {captured_ids}"
    )

    assert result.get("epic_id") == epic_id, result
    assert result.get("submitted") == 3, result
    results = result.get("results") or {}
    assert set(results.keys()) == set(child_ids), (
        f"expected results for {sorted(child_ids)}, got {sorted(results.keys())}"
    )
    for cid, child_result in results.items():
        assert isinstance(child_result, dict), (cid, child_result)
        assert child_result.get("status") == "ok", (cid, child_result)


def test_epic_run_from_outside_cwd_without_rig_path_finds_zero_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: simulates the pre-3mw behaviour by NOT passing
    rig_path through traversal.

    This test deliberately calls `list_epic_children` directly without
    `rig_path` from a non-rig cwd — confirming that the bug repro relies
    on cwd inheritance and that the fix's opt-in cwd kwarg is what
    rescues callers. If this test starts returning 3 children, the cwd
    inheritance is no longer the failure mode (which would be a Good
    Thing™ but should still flag).
    """
    rig = tmp_path / "rig"
    _bd_init(rig)
    epic_id = _bd_q(rig, "Epic", "--type", "epic")
    child_ids = [_bd_q(rig, f"c{i}", "--type", "task") for i in range(1, 4)]
    for child in child_ids:
        _bd_dep_add(rig, epic_id, child)

    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)

    from prefect_orchestration.beads_meta import list_epic_children

    # Pre-3mw behaviour: without rig_path, bd shellouts use the test's cwd.
    # `outside` has no `.beads/` so discovery returns 0.
    nodes_no_rig_path = list_epic_children(epic_id, mode="deps")
    assert nodes_no_rig_path == [], (
        f"without rig_path from a non-rig cwd, expected 0 nodes; "
        f"got {nodes_no_rig_path}"
    )

    # With rig_path: the fix kicks in and all 3 children surface.
    nodes_with_rig = list_epic_children(epic_id, mode="deps", rig_path=rig)
    assert sorted(n["id"] for n in nodes_with_rig) == sorted(child_ids)
