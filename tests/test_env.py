"""Unit tests for `prefect_orchestration.env`."""

from __future__ import annotations

import os

import pytest
from typer.testing import CliRunner

from prefect_orchestration.env import (
    EnvNotFound,
    EnvRecord,
    delete_env,
    env_app,
    list_envs,
    read_env,
    write_env,
)
from prefect_orchestration.env_drivers import NoopDriver

runner = CliRunner()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_record(name: str = "myenv", driver: str = "noop") -> EnvRecord:
    return EnvRecord(
        name=name,
        driver=driver,
        snapshot_tag="v1",
        pool=f"po-env-{name}",
        opaque={"sandbox_id": "abc", "nested": [1, 2]},
        rig_remote="",
        identity_hash="deadbeef",
        created_at="2026-05-13T00:00:00+00:00",
        last_run_at="",
    )


# ---------------------------------------------------------------------------
# TOML round-trip
# ---------------------------------------------------------------------------


def test_write_read_env_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("prefect_orchestration.env.ENVS_DIR", tmp_path / "envs")
    import prefect_orchestration.env as env_mod

    monkeypatch.setattr(env_mod, "ENVS_DIR", tmp_path / "envs")

    rec = _make_record()
    write_env(rec)

    got = read_env("myenv")
    assert got.name == rec.name
    assert got.driver == rec.driver
    assert got.snapshot_tag == rec.snapshot_tag
    assert got.pool == rec.pool
    assert got.opaque == rec.opaque
    assert got.rig_remote == rec.rig_remote
    assert got.identity_hash == rec.identity_hash
    assert got.created_at == rec.created_at
    assert got.last_run_at == rec.last_run_at


def test_read_env_not_found_raises(tmp_path, monkeypatch):
    import prefect_orchestration.env as env_mod

    monkeypatch.setattr(env_mod, "ENVS_DIR", tmp_path / "envs")
    with pytest.raises(EnvNotFound):
        read_env("nosuchenv")


def test_list_envs_empty(tmp_path, monkeypatch):
    import prefect_orchestration.env as env_mod

    monkeypatch.setattr(env_mod, "ENVS_DIR", tmp_path / "envs")
    assert list_envs() == []


def test_list_envs_multiple(tmp_path, monkeypatch):
    import prefect_orchestration.env as env_mod

    monkeypatch.setattr(env_mod, "ENVS_DIR", tmp_path / "envs")
    write_env(_make_record("alpha"))
    write_env(_make_record("beta"))

    records = list_envs()
    names = {r.name for r in records}
    assert names == {"alpha", "beta"}


def test_delete_env_removes_file(tmp_path, monkeypatch):
    import prefect_orchestration.env as env_mod

    monkeypatch.setattr(env_mod, "ENVS_DIR", tmp_path / "envs")
    write_env(_make_record())
    assert (tmp_path / "envs" / "myenv.toml").exists()

    delete_env("myenv")
    assert not (tmp_path / "envs" / "myenv.toml").exists()


# ---------------------------------------------------------------------------
# CLI: env up
# ---------------------------------------------------------------------------


def test_env_up_noop_creates_record(tmp_path, monkeypatch):
    import prefect_orchestration.env as env_mod

    envs_dir = tmp_path / "envs"
    monkeypatch.setattr(env_mod, "ENVS_DIR", envs_dir)

    noop = NoopDriver()
    monkeypatch.setattr(env_mod, "load_drivers", lambda: {"noop": noop})

    # suppress subprocess (prefect work-pool create)
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: type("R", (), {"returncode": 0})(),
    )
    # suppress identity tarball (no ~/.claude/ needed)
    monkeypatch.setattr(
        env_mod,
        "_build_identity_tarball",
        lambda dest, *, with_auth: (dest / "fake.tar.gz", "abc123"),
    )

    result = runner.invoke(env_app, ["up", "--driver", "noop", "--name", "myenv"])
    assert result.exit_code == 0, result.output
    assert (envs_dir / "myenv.toml").exists()
    assert any(c[0] == "provision" for c in noop.calls)


def test_env_up_backend_threaded_into_opts(tmp_path, monkeypatch):
    """--backend value is forwarded to driver.provision() inside opts."""
    import prefect_orchestration.env as env_mod

    envs_dir = tmp_path / "envs"
    monkeypatch.setattr(env_mod, "ENVS_DIR", envs_dir)

    noop = NoopDriver()
    monkeypatch.setattr(env_mod, "load_drivers", lambda: {"noop": noop})

    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: type("R", (), {"returncode": 0})(),
    )
    monkeypatch.setattr(
        env_mod,
        "_build_identity_tarball",
        lambda dest, *, with_auth: (dest / "fake.tar.gz", "abc123"),
    )

    result = runner.invoke(
        env_app,
        ["up", "--driver", "noop", "--name", "myenv", "--backend", "digitalocean"],
    )
    assert result.exit_code == 0, result.output
    provision_calls = [c for c in noop.calls if c[0] == "provision"]
    assert provision_calls, "provision() was not called"
    opts = provision_calls[0][3]  # (provision, name, snapshot_tag, opts_dict)
    assert opts.get("backend") == "digitalocean"


def test_env_up_unknown_driver_exits_1(tmp_path, monkeypatch):
    import prefect_orchestration.env as env_mod

    monkeypatch.setattr(env_mod, "ENVS_DIR", tmp_path / "envs")
    monkeypatch.setattr(env_mod, "load_drivers", lambda: {})

    result = runner.invoke(env_app, ["up", "--driver", "bogus", "--name", "x"])
    assert result.exit_code == 1
    assert "unknown driver" in result.output


def test_env_up_invalid_name_exits_1(tmp_path, monkeypatch):
    import prefect_orchestration.env as env_mod

    monkeypatch.setattr(env_mod, "ENVS_DIR", tmp_path / "envs")
    monkeypatch.setattr(env_mod, "load_drivers", lambda: {"noop": NoopDriver()})

    result = runner.invoke(env_app, ["up", "--driver", "noop", "--name", "bad name!"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# CLI: env down
# ---------------------------------------------------------------------------


def test_env_down_removes_record(tmp_path, monkeypatch):
    import prefect_orchestration.env as env_mod

    envs_dir = tmp_path / "envs"
    monkeypatch.setattr(env_mod, "ENVS_DIR", envs_dir)

    noop = NoopDriver()
    monkeypatch.setattr(env_mod, "load_drivers", lambda: {"noop": noop})

    write_env(_make_record())
    result = runner.invoke(env_app, ["down", "myenv", "-f"])
    assert result.exit_code == 0, result.output
    assert not (envs_dir / "myenv.toml").exists()
    assert any(c[0] == "teardown" for c in noop.calls)


def test_env_down_missing_env_exits_1(tmp_path, monkeypatch):
    import prefect_orchestration.env as env_mod

    monkeypatch.setattr(env_mod, "ENVS_DIR", tmp_path / "envs")
    monkeypatch.setattr(env_mod, "load_drivers", lambda: {})

    result = runner.invoke(env_app, ["down", "nosuchenv", "-f"])
    assert result.exit_code == 1
    assert "no env" in result.output


# ---------------------------------------------------------------------------
# CLI: env attach
# ---------------------------------------------------------------------------


def test_env_attach_execvp(tmp_path, monkeypatch):
    import prefect_orchestration.env as env_mod

    monkeypatch.setattr(env_mod, "ENVS_DIR", tmp_path / "envs")
    noop = NoopDriver()
    monkeypatch.setattr(env_mod, "load_drivers", lambda: {"noop": noop})

    execvp_calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        os, "execvp", lambda prog, argv: execvp_calls.append((prog, argv))
    )

    write_env(_make_record())
    runner.invoke(env_app, ["attach", "myenv"])
    # NoopDriver.attach_argv returns ["true"] so execvp would be called
    assert any(c[0] == "attach_argv" for c in noop.calls)
    assert execvp_calls[0] == ("true", ["true"])


def test_env_attach_missing_env_exits_1(tmp_path, monkeypatch):
    import prefect_orchestration.env as env_mod

    monkeypatch.setattr(env_mod, "ENVS_DIR", tmp_path / "envs")
    monkeypatch.setattr(env_mod, "load_drivers", lambda: {})

    result = runner.invoke(env_app, ["attach", "nosuchenv"])
    assert result.exit_code == 1
    assert "no env" in result.output


# ---------------------------------------------------------------------------
# CLI: env list
# ---------------------------------------------------------------------------


def test_env_list_shows_records(tmp_path, monkeypatch):
    import prefect_orchestration.env as env_mod

    monkeypatch.setattr(env_mod, "ENVS_DIR", tmp_path / "envs")
    write_env(_make_record("alpha"))
    write_env(_make_record("beta"))

    result = runner.invoke(env_app, ["list"])
    assert result.exit_code == 0
    assert "alpha" in result.output
    assert "beta" in result.output


def test_env_list_empty(tmp_path, monkeypatch):
    import prefect_orchestration.env as env_mod

    monkeypatch.setattr(env_mod, "ENVS_DIR", tmp_path / "envs")

    result = runner.invoke(env_app, ["list"])
    assert result.exit_code == 0
    assert "no envs" in result.output
