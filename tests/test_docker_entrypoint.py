"""Shell-level tests for `docker/entrypoint.sh`.

The entrypoint runs inside the worker container, so we exercise it
under bash with a tmp `HOME`. No Docker required — these run in CI on
any host with bash.

Covers:
  * OAuth mode: CLAUDE_CREDENTIALS env var -> $HOME/.claude/.credentials.json
    (mode 0600), ANTHROPIC_API_KEY scrubbed, ~/.claude.json drops the
    customApiKeyResponses block.
  * OAuth-via-bind-mount: pre-existing credentials file flips mode to
    oauth without a CLAUDE_CREDENTIALS env var.
  * API-key fallback: no CLAUDE_CREDENTIALS -> ~/.claude.json contains
    customApiKeyResponses and the last-20 of the key; no credentials
    file written.
  * Hard fail: neither auth set, real backend -> exit 64.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO_ROOT / "docker" / "entrypoint.sh"


def _run(env: dict[str, str], home: Path, *cmd: str) -> subprocess.CompletedProcess:
    """Invoke the entrypoint under a sanitized environment and tmp HOME."""
    base = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
    }
    base.update(env)
    return subprocess.run(
        ["bash", str(ENTRYPOINT), *cmd],
        env=base,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path


def test_oauth_mode_materializes_credentials(home: Path) -> None:
    cred_blob = '{"access_token": "oat-fake", "refresh_token": "ort-fake"}'
    res = _run(
        {
            "CLAUDE_CREDENTIALS": cred_blob,
            "ANTHROPIC_API_KEY": "sk-should-be-scrubbed",
            "PO_BACKEND": "cli",
        },
        home,
        "/usr/bin/env",
    )
    assert res.returncode == 0, res.stderr

    cred_path = home / ".claude" / ".credentials.json"
    assert cred_path.exists()
    assert cred_path.read_text() == cred_blob
    mode = stat.S_IMODE(cred_path.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"

    # PO_AUTH_MODE exported to the child process
    assert "PO_AUTH_MODE=oauth" in res.stdout
    assert "PO_AUTH_SOURCE=env" in res.stdout
    # ANTHROPIC_API_KEY scrubbed before exec
    assert "ANTHROPIC_API_KEY=" not in res.stdout
    # CLAUDE_CREDENTIALS scrubbed before exec
    assert "CLAUDE_CREDENTIALS=" not in res.stdout

    cfg = json.loads((home / ".claude.json").read_text())
    assert cfg["hasCompletedOnboarding"] is True
    # OAuth mode drops the API-key approval block
    assert "customApiKeyResponses" not in cfg


def test_oauth_env_does_not_overwrite_existing_credential_file(home: Path) -> None:
    """tyf.3: when both CLAUDE_CREDENTIALS env and an on-disk credentials
    file exist, the on-disk file wins. This is what makes a PVC mount
    actually persistent — the Claude CLI refreshes the token in place,
    and the next pod start reads the fresh file instead of being
    clobbered by the (stale) Secret payload."""
    cred_dir = home / ".claude"
    cred_dir.mkdir()
    cred_path = cred_dir / ".credentials.json"
    on_disk = '{"access_token": "fresh-from-pvc", "refresh_token": "rt-fresh"}'
    cred_path.write_text(on_disk)
    cred_path.chmod(0o600)
    pre_mtime = cred_path.stat().st_mtime_ns

    res = _run(
        {
            "CLAUDE_CREDENTIALS": '{"access_token": "stale-from-secret"}',
            "ANTHROPIC_API_KEY": "sk-shouldnt-be-used",
            "PO_BACKEND": "cli",
        },
        home,
        "/usr/bin/env",
    )
    assert res.returncode == 0, res.stderr

    # File contents and mtime unchanged → no overwrite.
    assert cred_path.read_text() == on_disk
    assert cred_path.stat().st_mtime_ns == pre_mtime

    assert "PO_AUTH_MODE=oauth" in res.stdout
    assert "PO_AUTH_SOURCE=disk" in res.stdout
    assert "CLAUDE_CREDENTIALS=" not in res.stdout
    assert "ANTHROPIC_API_KEY=" not in res.stdout


def test_oauth_via_bindmount_credential_file(home: Path) -> None:
    """Pre-existing credentials file (bind-mount case) flips mode to oauth
    without CLAUDE_CREDENTIALS env var, and the entrypoint does not
    overwrite it."""
    cred_dir = home / ".claude"
    cred_dir.mkdir()
    cred_path = cred_dir / ".credentials.json"
    original = '{"access_token": "from-bind-mount"}'
    cred_path.write_text(original)
    cred_path.chmod(0o600)

    res = _run(
        {"ANTHROPIC_API_KEY": "sk-fallback-shouldnt-be-used", "PO_BACKEND": "cli"},
        home,
        "/usr/bin/env",
    )
    assert res.returncode == 0, res.stderr
    assert cred_path.read_text() == original
    assert "PO_AUTH_MODE=oauth" in res.stdout
    assert "PO_AUTH_SOURCE=disk" in res.stdout
    assert "ANTHROPIC_API_KEY=" not in res.stdout

    cfg = json.loads((home / ".claude.json").read_text())
    assert "customApiKeyResponses" not in cfg


def test_apikey_fallback_when_no_oauth(home: Path) -> None:
    api_key = "sk-test-1234567890abcdefghij"
    res = _run(
        {"ANTHROPIC_API_KEY": api_key, "PO_BACKEND": "cli"},
        home,
        "/usr/bin/env",
    )
    assert res.returncode == 0, res.stderr

    cred_path = home / ".claude" / ".credentials.json"
    assert not cred_path.exists()

    assert "PO_AUTH_MODE=apikey" in res.stdout
    cfg = json.loads((home / ".claude.json").read_text())
    assert "customApiKeyResponses" in cfg
    # Approval block keys on the last 20 chars of the key
    assert cfg["customApiKeyResponses"]["approved"] == [api_key[-20:]]


def test_no_auth_real_backend_exits_64(home: Path) -> None:
    res = _run({"PO_BACKEND": "cli"}, home, "/usr/bin/env")
    assert res.returncode == 64
    assert "no Claude auth configured" in res.stderr


def test_stub_backend_skips_auth_requirement(home: Path) -> None:
    res = _run({"PO_BACKEND": "stub"}, home, "/usr/bin/env")
    assert res.returncode == 0, res.stderr
    assert "PO_AUTH_MODE=apikey" in res.stdout


def test_entrypoint_does_not_set_x() -> None:
    """Guardrail: entrypoint must never enable shell trace mode (`set -x`)
    because it would echo CLAUDE_CREDENTIALS / ANTHROPIC_API_KEY to logs."""
    text = ENTRYPOINT.read_text()
    # `set -x` as a standalone directive (not the `set -euo pipefail` line)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert stripped != "set -x"
        assert "set -x" not in stripped or "set -euo" in stripped


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_entrypoint_is_executable() -> None:
    """The Dockerfile chmod +x's the entrypoint at build time, but the
    on-disk file should at minimum be a syntactically valid bash script."""
    res = subprocess.run(
        ["bash", "-n", str(ENTRYPOINT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
