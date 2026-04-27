"""Unit tests for `check_beads_dolt_mode` (issue prefect-orchestration-3j8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prefect_orchestration.doctor import Status, check_beads_dolt_mode


def _write_meta(rig: Path, payload: dict) -> None:
    beads = rig / ".beads"
    beads.mkdir(parents=True, exist_ok=True)
    (beads / "metadata.json").write_text(json.dumps(payload))


def test_no_beads_dir_is_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    res = check_beads_dolt_mode()
    assert res.status is Status.OK
    assert "no .beads/" in res.message


def test_dolt_server_mode_is_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_meta(
        tmp_path,
        {"dolt_mode": "server", "dolt_database": "demo", "dolt_host": "127.0.0.1"},
    )
    monkeypatch.chdir(tmp_path)
    res = check_beads_dolt_mode()
    assert res.status is Status.OK
    assert "dolt-server" in res.message
    assert "demo" in res.message


def test_embedded_mode_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_meta(tmp_path, {"dolt_mode": "embedded"})
    monkeypatch.chdir(tmp_path)
    res = check_beads_dolt_mode()
    assert res.status is Status.WARN
    assert "embedded" in res.message
    assert "bd init --server" in res.remediation


def test_missing_mode_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_meta(tmp_path, {"backend": "dolt"})
    monkeypatch.chdir(tmp_path)
    res = check_beads_dolt_mode()
    assert res.status is Status.WARN


def test_unreadable_metadata_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    beads = tmp_path / ".beads"
    beads.mkdir()
    (beads / "metadata.json").write_text("{not json")
    monkeypatch.chdir(tmp_path)
    res = check_beads_dolt_mode()
    assert res.status is Status.WARN
    assert "unreadable" in res.message
