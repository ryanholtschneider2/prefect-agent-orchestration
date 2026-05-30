"""Unit tests for the daytona branch of RClaudeEnvDriver (no network).

Guarded by importorskip("rclaude"); patches DaytonaBackend/DaytonaDevEnv with
fakes so nothing hits Daytona Cloud. Live verification needs a DAYTONA_API_KEY
and capacity (see the rclaude repo's daytona probe scripts).
"""

from __future__ import annotations

import types
from unittest.mock import patch

import pytest

from prefect_orchestration.env_drivers import EnvHandle

pytest.importorskip("rclaude", reason="rclaude not installed")

from po_formulas_cloud_rclaude.driver import RClaudeEnvDriver  # noqa: E402


class FakeBackend:
    def __init__(self):
        self.calls = []

    def provision_vm(self, *, name, from_snapshot="", tags=None, auto_stop_minutes=0, **kw):
        self.calls.append(("provision_vm", from_snapshot, tuple(tags or []), auto_stop_minutes))
        return types.SimpleNamespace(id="sb-77")

    def destroy_vm(self, sid):
        self.calls.append(("destroy_vm", sid))

    def exec(self, sid, command):
        self.calls.append(("exec", sid, " ".join(command)))
        return ""

    def upload_text(self, sid, path, content, mode="644"):
        self.calls.append(("upload_text", path, mode))

    def upload_bytes(self, sid, path, data):
        self.calls.append(("upload_bytes", path, len(data)))

    def download_bytes(self, sid, path):
        self.calls.append(("download_bytes", path))
        return b""

    def ssh_access(self, sid, expires_in_minutes=60):
        return types.SimpleNamespace(ssh_command="ssh tok-1@ssh.app.daytona.io")


def _handle():
    return EnvHandle(
        driver_name="rclaude",
        opaque={"backend": "daytona", "sandbox_id": "sb-77", "host": "", "api_url": ""},
    )


def test_provision_daytona(monkeypatch):
    be = FakeBackend()
    ensured = {"n": 0}
    fake_dev = types.SimpleNamespace(
        ensure_base_snapshot=lambda *a, **k: ensured.__setitem__("n", ensured["n"] + 1)
    )
    with (
        patch("rclaude.backends.daytona_backend.DaytonaBackend", lambda: be),
        patch("rclaude.daytona_devenv.DaytonaDevEnv", lambda _b: fake_dev),
    ):
        h = RClaudeEnvDriver().provision("polymer", "", {"backend": "daytona"})
    assert h.opaque["backend"] == "daytona"
    assert h.opaque["sandbox_id"] == "sb-77"
    assert ensured["n"] == 1  # base snapshot ensured (default snapshot)
    prov = next(c for c in be.calls if c[0] == "provision_vm")
    assert prov[3] == 0  # auto_stop disabled so the PO worker stays alive


def test_teardown_daytona_scrubs_then_deletes():
    be = FakeBackend()
    with patch.object(RClaudeEnvDriver, "_daytona_backend", return_value=be):
        RClaudeEnvDriver().teardown(_handle())
    kinds = [c[0] for c in be.calls]
    assert "exec" in kinds and "destroy_vm" in kinds
    assert kinds.index("exec") < kinds.index("destroy_vm")  # scrub before delete
    assert any("rclaude" in c[2] or "shm" in c[2] for c in be.calls if c[0] == "exec")


def test_push_credentials_daytona_tmpfs_and_oauth():
    be = FakeBackend()
    with patch.object(RClaudeEnvDriver, "_daytona_backend", return_value=be):
        RClaudeEnvDriver().push_credentials(
            _handle(), {"ANTHROPIC_API_KEY": "sk-x"}, b'{"oauth":1}'
        )
    # secret payload written to tmpfs via exec
    assert any(c[0] == "exec" and "/dev/shm/rclaude/secrets.env" in c[2] for c in be.calls)
    # OAuth creds uploaded to disk at 0600
    assert any(
        c[0] == "upload_text" and c[1].endswith(".claude/.credentials.json") and c[2] == "600"
        for c in be.calls
    )


def test_attach_argv_daytona_uses_ssh_gateway():
    be = FakeBackend()
    with patch.object(RClaudeEnvDriver, "_daytona_backend", return_value=be):
        argv = RClaudeEnvDriver().attach_argv(_handle(), "builder", "polymer-1")
    assert argv[0] == "ssh" and "-t" in argv
    assert "tok-1@ssh.app.daytona.io" in argv
    assert argv[-1] == "tmux attach -t po-polymer-1-builder"


def test_health_daytona():
    be = FakeBackend()
    with patch.object(RClaudeEnvDriver, "_daytona_backend", return_value=be):
        h = RClaudeEnvDriver().health(_handle())
    assert h.ok and "daytona" in h.summary


def test_ensure_rig_remote_daytona_is_tar_fallback():
    assert RClaudeEnvDriver().ensure_rig_remote(_handle()) == ""


def test_suspend_resume_daytona_calls_backend():
    calls = []
    be = types.SimpleNamespace(
        stop_vm=lambda sid: calls.append(("stop", sid)),
        start_vm=lambda sid: calls.append(("start", sid)),
    )
    with patch.object(RClaudeEnvDriver, "_daytona_backend", return_value=be):
        d = RClaudeEnvDriver()
        d.suspend(_handle())
        d.resume(_handle())
    assert calls == [("stop", "sb-77"), ("start", "sb-77")]


def test_suspend_rejected_on_non_daytona():
    h = EnvHandle(driver_name="rclaude", opaque={"backend": "ssh", "ssh_target": "x"})
    with pytest.raises(NotImplementedError, match="daytona"):
        RClaudeEnvDriver().suspend(h)


def test_sync_packs_daytona_delivers_and_installs(monkeypatch, tmp_path):
    """Daytona sync-packs tars sources over exec, then uv tool installs them."""
    # Fake two editable packs on disk.
    core = tmp_path / "prefect-orchestration"
    pack = tmp_path / "po-formulas-software-dev"
    for p in (core, pack):
        (p).mkdir()
        (p / "pyproject.toml").write_text("[project]\n")

    be = FakeBackend()
    d = RClaudeEnvDriver()
    monkeypatch.setattr(
        d, "_local_editable_packs",
        lambda: [("prefect-orchestration", str(core)), ("po-formulas-software-dev", str(pack))],
    )
    with patch.object(RClaudeEnvDriver, "_daytona_backend", return_value=be):
        d.sync_packs(_handle())

    # two pack tarballs uploaded via the fs API, each unpacked via exec
    assert sum(1 for c in be.calls if c[0] == "upload_bytes") == 2
    execs = [c[2] for c in be.calls if c[0] == "exec"]
    assert sum("tar -xzf" in e for e in execs) == 2
    install = next(e for e in execs if "uv tool install" in e)
    assert "--editable" in install and "--with-editable" in install
    assert ".po-packs/prefect-orchestration" in install
    assert "--with socksio" in install  # SOCKS support for the worker's httpx


def test_start_worker_daytona_wires_tailscale():
    be = FakeBackend()
    with patch.object(RClaudeEnvDriver, "_daytona_backend", return_value=be):
        RClaudeEnvDriver().start_worker(_handle(), "po-env-x")
    script = next(c[2] for c in be.calls if c[0] == "exec")
    # Conditional tailnet join + SOCKS5 egress so it can reach a private Prefect.
    assert 'TS_AUTHKEY' in script and "tailscale up" in script
    assert "socks5-server=localhost:1055" in script
    assert "ALL_PROXY=socks5h://localhost:1055" in script
    assert "prefect worker start --pool po-env-x" in script
