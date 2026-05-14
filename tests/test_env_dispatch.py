"""Unit tests for `prefect_orchestration.env_dispatch`."""

from __future__ import annotations


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
    monkeypatch.setattr(
        ed, "read_env", lambda name: (_ for _ in ()).throw(EnvNotFound(name))
    )
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

    record = _make_record(identity_hash="match-hash")
    noop = NoopDriver()

    monkeypatch.setattr(
        ed,
        "_build_identity_tarball",
        lambda dest, with_auth=False: (dest / "x.tar.gz", "match-hash"),
    )
    # Simulate ~/.claude/ existing
    monkeypatch.setattr(ed.Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".claude").mkdir(exist_ok=True)

    ed._maybe_push_identity(record, EnvHandle(driver_name="noop", opaque={}), noop)
    assert not any(c[0] == "push_identity" for c in noop.calls)


def test_run_with_env_push_identity_called_when_hash_differs(monkeypatch, tmp_path):
    """_maybe_push_identity uploads when hash changed and updates record."""
    record = _make_record(identity_hash="old-hash")
    noop = NoopDriver()

    monkeypatch.setattr(
        ed,
        "_build_identity_tarball",
        lambda dest, with_auth=False: (dest / "x.tar.gz", "new-hash"),
    )
    monkeypatch.setattr(ed.Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".claude").mkdir(exist_ok=True)

    ed._maybe_push_identity(record, EnvHandle(driver_name="noop", opaque={}), noop)
    assert any(c[0] == "push_identity" for c in noop.calls)
    assert record.identity_hash == "new-hash"


# ---------------------------------------------------------------------------
# run_ephemeral_env tests
# ---------------------------------------------------------------------------


def _patch_ephemeral(monkeypatch, tmp_path, noop, terminal_state="Completed"):
    """Apply common monkeypatches for run_ephemeral_env tests."""
    monkeypatch.setattr(ed, "load_drivers", lambda: {"noop": noop})
    monkeypatch.setattr(ed, "write_env", lambda r: None)
    monkeypatch.setattr(ed, "delete_env", lambda name: None)
    monkeypatch.setattr(
        ed,
        "_build_identity_tarball",
        lambda dest, with_auth=False: (dest / "id.tar.gz", "hash-xyz"),
    )
    monkeypatch.setattr(ed.Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".claude").mkdir(exist_ok=True)
    monkeypatch.setattr(ed, "run_with_env", lambda **kw: terminal_state)
    monkeypatch.setattr(ed.subprocess, "run", lambda *a, **kw: None)
    monkeypatch.setattr(ed.time, "sleep", lambda s: None)


def test_run_ephemeral_teardown_on_completed(monkeypatch, tmp_path):
    """Teardown is called when flow Completes."""
    noop = NoopDriver()
    _patch_ephemeral(monkeypatch, tmp_path, noop, terminal_state="Completed")
    teardown_calls: list[str] = []
    delete_calls: list[str] = []
    monkeypatch.setattr(ed, "delete_env", lambda name: delete_calls.append(name))
    original_teardown = noop.teardown

    def _patched_teardown(handle):
        teardown_calls.append(handle.driver_name)
        return original_teardown(handle)

    noop.teardown = _patched_teardown  # type: ignore[method-assign]

    ed.run_ephemeral_env(
        driver_name="noop",
        formula="software-dev-fast",
        kwargs={},
        auto_down_secs=0,
    )
    assert teardown_calls, "teardown should be called on Completed"
    assert delete_calls, "delete_env should be called on Completed"


def test_run_ephemeral_no_teardown_on_failure_by_default(monkeypatch, tmp_path):
    """Env is kept alive on Failed when auto_down_on_failure=False (default)."""
    noop = NoopDriver()
    _patch_ephemeral(monkeypatch, tmp_path, noop, terminal_state="Failed")
    delete_calls: list[str] = []
    monkeypatch.setattr(ed, "delete_env", lambda name: delete_calls.append(name))

    ed.run_ephemeral_env(
        driver_name="noop",
        formula="software-dev-fast",
        kwargs={},
        auto_down_secs=0,
        auto_down_on_failure=False,
    )
    assert not any(c[0] == "teardown" for c in noop.calls), (
        "teardown must NOT be called on Failed by default"
    )
    assert not delete_calls, "delete_env must NOT be called on Failed by default"


def test_run_ephemeral_teardown_on_failure_with_flag(monkeypatch, tmp_path):
    """Teardown IS called on Failed when auto_down_on_failure=True."""
    noop = NoopDriver()
    _patch_ephemeral(monkeypatch, tmp_path, noop, terminal_state="Failed")
    delete_calls: list[str] = []
    monkeypatch.setattr(ed, "delete_env", lambda name: delete_calls.append(name))

    ed.run_ephemeral_env(
        driver_name="noop",
        formula="software-dev-fast",
        kwargs={},
        auto_down_secs=0,
        auto_down_on_failure=True,
    )
    assert any(c[0] == "teardown" for c in noop.calls), (
        "teardown must be called when auto_down_on_failure=True"
    )
    assert delete_calls, "delete_env must be called when auto_down_on_failure=True"


def test_run_ephemeral_grace_window_called(monkeypatch, tmp_path):
    """time.sleep is called with auto_down_secs before teardown."""
    noop = NoopDriver()
    _patch_ephemeral(monkeypatch, tmp_path, noop, terminal_state="Completed")
    sleep_calls: list[float] = []
    monkeypatch.setattr(ed.time, "sleep", lambda s: sleep_calls.append(s))

    ed.run_ephemeral_env(
        driver_name="noop",
        formula="software-dev-fast",
        kwargs={},
        auto_down_secs=999.0,
    )
    assert sleep_calls == [999.0], f"expected sleep(999.0), got {sleep_calls}"


def test_run_ephemeral_build_image_called_if_driver_has_it(monkeypatch, tmp_path):
    """build_image() is invoked on NoopDriver (which implements it)."""
    noop = NoopDriver()
    _patch_ephemeral(monkeypatch, tmp_path, noop, terminal_state="Completed")

    ed.run_ephemeral_env(
        driver_name="noop",
        formula="software-dev-fast",
        kwargs={},
        auto_down_secs=0,
    )
    assert ("build_image", {}) in noop.calls, (
        "build_image should be called on drivers that implement it"
    )
