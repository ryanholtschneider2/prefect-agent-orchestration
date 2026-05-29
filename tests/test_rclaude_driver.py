"""Unit tests for po_formulas_cloud_rclaude.driver.RClaudeEnvDriver.

All tests are guarded by pytest.importorskip("rclaude") so they are skipped
when rclaude is not installed (CI without --ignore-requires-python).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from prefect_orchestration.env_drivers import EnvHandle

# Guard: skip entire module when rclaude is absent
rclaude = pytest.importorskip("rclaude", reason="rclaude not installed")

from po_formulas_cloud_rclaude.driver import RClaudeEnvDriver  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_handle(
    ip: str = "1.2.3.4",
    droplet_id: str = "d-123",
    backend: str = "digitalocean",
    ssh_key: str = "/tmp/test.key",
) -> EnvHandle:
    return EnvHandle(
        driver_name="rclaude",
        opaque={
            "ip": ip,
            "droplet_id": droplet_id,
            "backend": backend,
            "ssh_key": ssh_key,
        },
    )


# ---------------------------------------------------------------------------
# provision
# ---------------------------------------------------------------------------


def test_provision_calls_launcher(monkeypatch):
    """provision() delegates to DevEnvLauncher and returns EnvHandle with expected fields."""
    fake_info = MagicMock()
    fake_info.droplet_id = "d-abc"
    fake_info.ip = "10.0.0.1"

    fake_launcher = MagicMock()
    fake_launcher.launch.return_value = fake_info

    fake_backend = MagicMock()
    fake_backend.ssh_key_path_for.return_value = Path("/home/user/.ssh/do")

    import po_formulas_cloud_rclaude.driver as drv_mod

    with (
        patch.object(drv_mod, "_RCLAUDE", True),
        patch(
            "po_formulas_cloud_rclaude.driver.DigitalOceanBackend",
            return_value=fake_backend,
        ),
        patch(
            "po_formulas_cloud_rclaude.driver.DevEnvLauncher",
            return_value=fake_launcher,
        ),
    ):
        driver = RClaudeEnvDriver()
        handle = driver.provision("myenv", "", {"backend": "digitalocean"})

    fake_launcher.launch.assert_called_once_with(name="myenv")
    assert handle.driver_name == "rclaude"
    assert handle.opaque["droplet_id"] == "d-abc"
    assert handle.opaque["ip"] == "10.0.0.1"
    assert handle.opaque["backend"] == "digitalocean"


def test_provision_missing_rclaude():
    """provision() raises RuntimeError when rclaude is not installed."""
    import po_formulas_cloud_rclaude.driver as drv_mod

    with patch.object(drv_mod, "_RCLAUDE", False):
        driver = RClaudeEnvDriver()
        with pytest.raises(RuntimeError, match="rclaude is not installed"):
            driver.provision("env", "")


# ---------------------------------------------------------------------------
# attach_argv
# ---------------------------------------------------------------------------


def test_attach_argv_with_role():
    """attach_argv with role + issue_safe returns SSH command targeting the tmux session."""
    driver = RClaudeEnvDriver()
    handle = _make_handle()
    argv = driver.attach_argv(handle, role="builder", issue_safe="9ws_7")

    assert argv[0] == "ssh"
    full = " ".join(argv)
    assert "tmux attach -t po-9ws_7-builder" in full
    assert "1.2.3.4" in full


def test_attach_argv_no_role():
    """attach_argv with empty role and issue_safe falls back to bare tmux attach."""
    driver = RClaudeEnvDriver()
    handle = _make_handle()
    argv = driver.attach_argv(handle, role="", issue_safe="")

    full = " ".join(argv)
    assert "tmux attach" in full
    assert "1.2.3.4" in full


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


def test_health_ssh_ok(monkeypatch):
    """health() returns EnvHealth(ok=True) when SSH responds."""
    driver = RClaudeEnvDriver()
    handle = _make_handle()
    monkeypatch.setattr(driver, "_ssh", lambda op, script, **kw: "ok")

    result = driver.health(handle)
    assert result.ok is True
    assert "reachable" in result.summary.lower()


def test_health_ssh_fail(monkeypatch):
    """health() returns EnvHealth(ok=False) when SSH raises."""
    driver = RClaudeEnvDriver()
    handle = _make_handle()
    monkeypatch.setattr(
        driver, "_ssh", MagicMock(side_effect=RuntimeError("Connection refused"))
    )

    result = driver.health(handle)
    assert result.ok is False
    assert "SSH failed" in result.summary


# ---------------------------------------------------------------------------
# build_image
# ---------------------------------------------------------------------------


def test_build_image_noop():
    """build_image() is a no-op (VM driver has no image to bake)."""
    driver = RClaudeEnvDriver()
    with patch("subprocess.run") as mock_run:
        driver.build_image({"rebuild": True})
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# ensure_rig_remote
# ---------------------------------------------------------------------------


def test_ensure_rig_remote_returns_url(monkeypatch):
    """ensure_rig_remote() returns the correct SSH URL after init."""
    driver = RClaudeEnvDriver()
    handle = _make_handle(ip="5.6.7.8")
    monkeypatch.setattr(driver, "_ssh", lambda op, script, **kw: "")

    url = driver.ensure_rig_remote(handle)
    assert url == "ssh://root@5.6.7.8/home/coder/rig-remote.git"


# ---------------------------------------------------------------------------
# ssh backend (own-host path)
# ---------------------------------------------------------------------------


def _ssh_handle() -> EnvHandle:
    return EnvHandle(
        driver_name="rclaude",
        opaque={
            "backend": "ssh",
            "host": "laptop",
            "ssh_target": "ryan@laptop.ts",
            "ssh_user": "ryan",
            "ssh_addr": "laptop.ts",
            "ssh_key": "/home/u/.ssh/id_rsa",
            "api_url": "",
        },
    )


def test_provision_ssh_resolves_host(monkeypatch):
    """provision(backend=ssh) resolves an rclaude host and stores ssh fields, no VM."""
    import po_formulas_cloud_rclaude.driver as drv_mod

    fake_spec = MagicMock()
    fake_spec.ssh_target = "ryan@laptop.ts"
    fake_spec.user = "ryan"
    fake_spec.address = "laptop.ts"
    fake_spec.key_path = "/home/u/.ssh/id_rsa"

    with (
        patch.object(drv_mod, "_RCLAUDE", True),
        patch.object(drv_mod, "_resolve_host", return_value=fake_spec),
        patch.object(drv_mod.Path, "exists", return_value=True),
    ):
        handle = RClaudeEnvDriver().provision("laptop", "", {"backend": "ssh"})

    assert handle.opaque["backend"] == "ssh"
    assert handle.opaque["ssh_target"] == "ryan@laptop.ts"
    assert "droplet_id" not in handle.opaque


def test_ssh_opts_targets_user_not_root():
    """_ssh_opts for ssh backend ends with user@addr, not root@ip."""
    driver = RClaudeEnvDriver()
    opts = driver._ssh_opts(_ssh_handle().opaque)
    assert opts[-1] == "ryan@laptop.ts"
    assert "-i" in opts and "/home/u/.ssh/id_rsa" in opts


def test_ensure_rig_remote_ssh_is_noop():
    """ssh backend uses the remote's existing working tree — no bare remote."""
    assert RClaudeEnvDriver().ensure_rig_remote(_ssh_handle()) == ""


def test_fs_download_ssh_mirrors_planning_root_by_issue(monkeypatch):
    """For ssh hosts, fs_download pulls every `.planning/*/<issue>/` subtree
    from the remote (formula-agnostic) into a dispatcher-local cache — not the
    dispatcher-guessed `<formula>` path that may not exist on the remote."""
    import po_formulas_cloud_rclaude.driver as drv_mod

    calls = {}
    monkeypatch.setattr(
        drv_mod.subprocess, "run", lambda argv, **kw: calls.setdefault("argv", argv)
    )
    drv = RClaudeEnvDriver()
    # core passes local_path = <remote-rig>/.planning/<guessed-formula>/<issue>
    remote_run = Path("/home/ryan/rig/.planning/software-dev-edit/abc")
    drv.fs_download(_ssh_handle(), ".planning/software-dev-edit/abc", remote_run)

    argv = calls["argv"]
    assert argv[0] == "rsync"
    # source is the remote .planning ROOT (not the guessed formula subdir)
    assert argv[-2] == "ryan@laptop.ts:/home/ryan/rig/.planning/"
    # include filter scopes to the issue across all formula dirs
    assert "--include=/*/abc/***" in argv
    assert argv[-1].endswith("/.cache/po/env-runs/")


def test_start_worker_ssh_sets_api_url_and_no_coder(monkeypatch):
    """start_worker on ssh host exports PREFECT_API_URL and never uses `su - coder`."""
    import po_formulas_cloud_rclaude.driver as drv_mod

    captured = {}
    monkeypatch.setattr(
        drv_mod, "_central_api_url", lambda stored="": "http://100.1.2.3:4200/api"
    )
    driver = RClaudeEnvDriver()
    monkeypatch.setattr(
        driver, "_ssh", lambda op, script, **kw: captured.setdefault("script", script)
    )
    driver.start_worker(_ssh_handle(), "po-env-laptop")

    script = captured["script"]
    assert "PREFECT_API_URL=http://100.1.2.3:4200/api" in script
    assert "su - coder" not in script
    assert "prefect worker start --pool po-env-laptop" in script
