"""Unit tests for `check_beads_dolt_mode`'s br (SQLite-WAL) branch (9xa)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from prefect_orchestration.doctor import Status, check_beads_dolt_mode


def _write_br_rig(rig: Path, journal_mode: str, *, make_db: bool = True) -> None:
    beads = rig / ".beads"
    beads.mkdir(parents=True, exist_ok=True)
    (beads / "metadata.json").write_text(
        json.dumps({"database": "beads.db", "jsonl_export": "issues.jsonl"})
    )
    if make_db:
        conn = sqlite3.connect(beads / "beads.db")
        try:
            conn.execute(f"PRAGMA journal_mode={journal_mode}")
            conn.execute("CREATE TABLE t (x INTEGER)")
        finally:
            conn.close()


def test_br_wal_is_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_br_rig(tmp_path, "WAL")
    monkeypatch.chdir(tmp_path)
    res = check_beads_dolt_mode()
    assert res.status is Status.OK
    assert "br" in res.message
    assert "WAL" in res.message


def test_br_non_wal_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_br_rig(tmp_path, "DELETE")
    monkeypatch.chdir(tmp_path)
    res = check_beads_dolt_mode()
    assert res.status is Status.WARN
    assert "not WAL" in res.message
    assert "journal_mode=WAL" in res.remediation


def test_br_missing_db_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_br_rig(tmp_path, "WAL", make_db=False)
    monkeypatch.chdir(tmp_path)
    res = check_beads_dolt_mode()
    assert res.status is Status.WARN
    assert "missing" in res.message
