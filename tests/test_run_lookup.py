"""Unit tests for prefect_orchestration.run_lookup."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from prefect_orchestration import run_lookup


def _fake_bd_show(metadata: dict | None):
    payload = {"id": "x", "metadata": metadata} if metadata is not None else {"id": "x"}

    def runner(cmd, capture_output, text, check):  # noqa: ARG001
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

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
