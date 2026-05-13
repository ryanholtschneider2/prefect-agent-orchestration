"""Unit tests for `prefect_orchestration.env_dispatch`."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import typer

import prefect_orchestration.env_dispatch as ed
from prefect_orchestration.env import EnvNotFound, EnvRecord
from prefect_orchestration.env_drivers import EnvHandle, NoopDriver


def _make_record(
    *,
    name: str = "myenv",
    driver: str = "noop",
    pool: str = "po-env-myenv",
    rig_remote: str = "",
    identity_hash: str = "abc123",
) -> EnvRecord:
    return EnvRecord(
        name=name,
        driver=driver,
        snapshot_tag="v1",
        pool=pool,
        opaque={},
        rig_remote=rig_remote,
        identity_hash=identity_hash,
        created_at="2026-01-01T00:00:00+00:00",
        last_run_at="",
    )


def test_run_with_env_missing_raises(monkeypatch):
    monkeypatch.setattr(ed, "read_env", lambda name: (_ for _ in ()).throw(EnvNotFound(name)))
    with pytest.raises(typer.Exit) as exc_info:
        ed.run_with_env(env_name="nonexistent", formula="f", kwargs={})
    assert exc_info.value.exit_code == 1


def test_run_with_env_missing_driver_raises(monkeypatch):
    record = _make_record(driver="missing-driver")
    monkeypatch.setattr(ed, "read_env", lambda name: record)
    monkeypatch.setattr(ed, "load_drivers", lambda: {})
    with pytest.raises(typer.Exit) as exc_info:
        ed.run_with_env(env_name="myenv", formula="f", kwargs={})
    assert exc_info.value.exit_code == 1


def test_run_with_env_rebuild_reprovisions(monkeypatch):
    record = _make_record()
    noop = NoopDriver()
    monkeypatch.setattr(ed, "read_env", lambda name: record)
    monkeypatch.setattr(ed, "load_drivers", lambda: {"noop": noop})
    monkeypatch.setattr(ed, "_push_rig", lambda *a, **kw: None)
    monkeypatch.setattr(ed, "_maybe_push_identity", lambda *a, **kw: None)
    monkeypatch.setattr(ed, "_stamp_bead", lambda *a, **kw: None)
    monkeypatch.setattr(ed, "_run_async_dispatch", lambda *a, **kw: "Completed")
    monkeypatch.setattr(ed, "write_env", lambda r: None)

    ed.run_with_env(
        env_name="myenv", formula="software-dev-fast", kwargs={}, rebuild=True
    )
    assert ("provision", "myenv", "v1", {"rebuild": True}) in noop.calls


def test_run_with_env_stamps_bead(monkeypatch):
    record = _make_record()
    noop = NoopDriver()
    stamp_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(ed, "read_env", lambda name: record)
    monkeypatch.setattr(ed, "load_drivers", lambda: {"noop": noop})
    monkeypatch.setattr(ed, "_push_rig", lambda *a, **kw: None)
    monkeypatch.setattr(ed, "_maybe_push_identity", lambda *a, **kw: None)
    monkeypatch.setattr(
        ed,
        "_stamp_bead",
        lambda issue_id, env_name: stamp_calls.append((issue_id, env_name)),
    )
    monkeypatch.setattr(ed, "_run_async_dispatch", lambda *a, **kw: "Completed")
    monkeypatch.setattr(ed, "write_env", lambda r: None)

    ed.run_with_env(
        env_name="myenv",
        formula="software-dev-fast",
        kwargs={"issue_id": "prefect-orchestration-test"},
        issue_id="prefect-orchestration-test",
    )
    assert stamp_calls == [("prefect-orchestration-test", "myenv")]


def test_run_with_env_calls_fs_download_on_terminal(monkeypatch, tmp_path):
    record = _make_record()
    noop = NoopDriver()

    monkeypatch.setattr(ed, "read_env", lambda name: record)
    monkeypatch.setattr(ed, "load_drivers", lambda: {"noop": noop})
    monkeypatch.setattr(ed, "_push_rig", lambda *a, **kw: None)
    monkeypatch.setattr(ed, "_maybe_push_identity", lambda *a, **kw: None)
    monkeypatch.setattr(ed, "_stamp_bead", lambda *a, **kw: None)
    monkeypatch.setattr(ed, "_run_async_dispatch", lambda *a, **kw: "Completed")
    monkeypatch.setattr(ed, "write_env", lambda r: None)

    rig_path = tmp_path
    ed.run_with_env(
        env_name="myenv",
        formula="software-dev-fast",
        kwargs={"issue_id": "po-abc"},
        issue_id="po-abc",
        rig_path=rig_path,
    )
    expected_remote = ".planning/software-dev-fast/po-abc"
    expected_local = str(rig_path / ".planning" / "software-dev-fast" / "po-abc")
    assert ("fs_download", expected_remote, expected_local) in noop.calls


def test_run_with_env_push_identity_skipped_when_hash_unchanged(monkeypatch, tmp_path):
    """_maybe_push_identity skips push when local hash equals stored hash."""
    from pathlib import Path

    record = _make_record(identity_hash="match-hash")
    noop = NoopDriver()

    monkeypatch.setattr(ed, "_build_identity_tarball", lambda dest, with_auth=False: (dest / "x.tar.gz", "match-hash"))
    # Simulate ~/.claude/ existing
    monkeypatch.setattr(ed.Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".claude").mkdir(exist_ok=True)

    ed._maybe_push_identity(record, EnvHandle(driver_name="noop", opaque={}), noop)
    assert not any(c[0] == "push_identity" for c in noop.calls)


def test_run_with_env_push_identity_called_when_hash_differs(monkeypatch, tmp_path):
    """_maybe_push_identity uploads when hash changed and updates record."""
    record = _make_record(identity_hash="old-hash")
    noop = NoopDriver()

    monkeypatch.setattr(ed, "_build_identity_tarball", lambda dest, with_auth=False: (dest / "x.tar.gz", "new-hash"))
    monkeypatch.setattr(ed.Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".claude").mkdir(exist_ok=True)

    ed._maybe_push_identity(record, EnvHandle(driver_name="noop", opaque={}), noop)
    assert any(c[0] == "push_identity" for c in noop.calls)
    assert record.identity_hash == "new-hash"
