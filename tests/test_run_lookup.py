"""Unit tests for prefect_orchestration.run_lookup."""

from __future__ import annotations

import json
import subprocess

import pytest

from prefect_orchestration import run_lookup


def _fake_bd_show(metadata: dict | None):
    payload = {"id": "x", "metadata": metadata} if metadata is not None else {"id": "x"}

    def runner(cmd, capture_output, text, check, cwd=None):  # noqa: ARG001
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(payload), stderr=""
        )

    return runner


def _fake_bd_show_cwd_required(metadata: dict, expected_cwd: str):
    """Returns a row only when cwd matches `expected_cwd` — simulates a
    bead that lives in rig A but is queried from rig B. Without cwd it
    fails like real bd does for a missing bead."""
    payload = {"id": "x", "metadata": metadata}

    def runner(cmd, capture_output, text, check, cwd=None):  # noqa: ARG001
        if cwd == expected_cwd:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(payload), stderr=""
            )
        return subprocess.CompletedProcess(
            cmd, 1, stdout="",
            stderr=f"Error fetching {cmd[2]}: no issue found matching {cmd[2]!r}",
        )

    return runner


def test_resolve_run_dir_happy_path(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setattr(run_lookup.shutil, "which", lambda _: "/usr/bin/bd")
    monkeypatch.setattr(
        run_lookup.subprocess,
        "run",
        _fake_bd_show({"po.rig_path": str(tmp_path), "po.run_dir": str(run_dir)}),
    )
    loc = run_lookup.resolve_run_dir("beads-xyz")
    assert loc.rig_path == tmp_path
    assert loc.run_dir == run_dir


def test_resolve_run_dir_missing_metadata_fix_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(run_lookup.shutil, "which", lambda _: "/usr/bin/bd")
    monkeypatch.setattr(run_lookup.subprocess, "run", _fake_bd_show({}))
    with pytest.raises(run_lookup.RunDirNotFound) as exc:
        run_lookup.resolve_run_dir("beads-xyz")
    msg = str(exc.value)
    assert "bd update beads-xyz" in msg
    assert "--set-metadata po.rig_path=" in msg
    assert "--set-metadata po.run_dir=" in msg


def test_resolve_run_dir_gone_from_disk(tmp_path, monkeypatch):
    missing = tmp_path / "does-not-exist"
    monkeypatch.setattr(run_lookup.shutil, "which", lambda _: "/usr/bin/bd")
    monkeypatch.setattr(
        run_lookup.subprocess,
        "run",
        _fake_bd_show({"po.rig_path": str(tmp_path), "po.run_dir": str(missing)}),
    )
    with pytest.raises(run_lookup.RunDirNotFound) as exc:
        run_lookup.resolve_run_dir("beads-xyz")
    assert "does not exist on disk" in str(exc.value)


def test_resolve_run_dir_bd_absent(monkeypatch):
    monkeypatch.setattr(run_lookup.shutil, "which", lambda _: None)
    with pytest.raises(run_lookup.RunDirNotFound) as exc:
        run_lookup.resolve_run_dir("beads-xyz")
    assert "`bd` CLI not on PATH" in str(exc.value)


def test_pick_freshest_chooses_max_mtime(tmp_path):
    a = tmp_path / "a.log"
    b = tmp_path / "b.log"
    a.write_text("a")
    b.write_text("b")
    import os

    os.utime(a, (1000, 1000))
    os.utime(b, (2000, 2000))
    picked = run_lookup.pick_freshest([a, b])
    assert picked == b


def test_pick_freshest_empty():
    assert run_lookup.pick_freshest([]) is None


def test_candidate_log_files_picks_run_dir_patterns(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "lint-iter-1.log").write_text("x")
    (run_dir / "test-iter-2.log").write_text("x")
    (run_dir / "decision-log.md").write_text("x")
    (run_dir / "irrelevant.txt").write_text("x")
    monkeypatch.setattr(run_lookup, "PREFECT_LOG_DIR", tmp_path / "nope")
    loc = run_lookup.RunLocation(rig_path=tmp_path, run_dir=run_dir)
    got = {p.name for p in run_lookup.candidate_log_files(loc)}
    assert got == {"lint-iter-1.log", "test-iter-2.log", "decision-log.md"}


# ─── cross-rig fallback via Prefect ──────────────────────────────────


def test_resolve_run_dir_cross_rig_via_prefect(tmp_path, monkeypatch):
    """Bead lives in rig A; user calls from rig B. First `bd show` from
    cwd misses with "no issue found". `rig_path_from_prefect` returns
    rig A's path; retry succeeds.

    Reproduces the rig-4lp confusion observed 2026-04-29: `po watch
    rig-4lp` from prefect-orchestration cwd failed because the bead was
    actually in a different rig. Now we resolve via Prefect and retry."""
    rig_a = tmp_path / "rig-a"
    rig_a.mkdir()
    run_dir = rig_a / "run"
    run_dir.mkdir()

    monkeypatch.setattr(run_lookup.shutil, "which", lambda _: "/usr/bin/bd")
    monkeypatch.setattr(
        run_lookup.subprocess, "run",
        _fake_bd_show_cwd_required(
            {"po.rig_path": str(rig_a), "po.run_dir": str(run_dir)},
            expected_cwd=str(rig_a),
        ),
    )
    monkeypatch.setattr(run_lookup, "rig_path_from_prefect", lambda _: rig_a)

    loc = run_lookup.resolve_run_dir("rig-4lp")
    assert loc.rig_path == rig_a
    assert loc.run_dir == run_dir


def test_resolve_run_dir_raises_when_prefect_also_misses(tmp_path, monkeypatch):
    """First bd-show miss + Prefect lookup returns None → original error
    bubbles up; user sees the bd-show stderr."""
    monkeypatch.setattr(run_lookup.shutil, "which", lambda _: "/usr/bin/bd")
    monkeypatch.setattr(
        run_lookup.subprocess, "run",
        _fake_bd_show_cwd_required(
            {"po.rig_path": str(tmp_path), "po.run_dir": str(tmp_path)},
            expected_cwd=str(tmp_path / "definitely-not-cwd"),
        ),
    )
    monkeypatch.setattr(run_lookup, "rig_path_from_prefect", lambda _: None)

    with pytest.raises(run_lookup.RunDirNotFound, match="bd show"):
        run_lookup.resolve_run_dir("ghost-id")
