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
from collections import Counter
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


# --------------------------------------------------------------------------
# Multi-account credential pool (5wk.3)
# --------------------------------------------------------------------------


def _run_with_hostname(
    env: dict[str, str], home: Path, hostname: str
) -> subprocess.CompletedProcess:
    base = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "HOSTNAME": hostname,
    }
    base.update(env)
    return subprocess.run(
        ["bash", str(ENTRYPOINT), "/usr/bin/env"],
        env=base,
        capture_output=True,
        text=True,
        check=False,
    )


def test_oauth_pool_picks_slot_by_ordinal_hostname(home: Path) -> None:
    """StatefulSet-style ordinal hostname (`worker-7`) → 7 % size."""
    pool = json.dumps([{"access_token": f"t{i}"} for i in range(3)])
    res = _run_with_hostname(
        {"CLAUDE_CREDENTIALS_POOL": pool, "PO_BACKEND": "cli"},
        home,
        hostname="worker-7",
    )
    assert res.returncode == 0, res.stderr
    # 7 % 3 == 1 → second element
    cred = json.loads((home / ".claude" / ".credentials.json").read_text())
    assert cred == {"access_token": "t1"}
    assert "pool index=1 size=3" in res.stderr
    assert "PO_AUTH_POOL_INDEX=1" in res.stdout
    assert "PO_AUTH_POOL_SIZE=3" in res.stdout
    # Pool env scrubbed before exec.
    assert "CLAUDE_CREDENTIALS_POOL=" not in res.stdout
    # Credential body must NOT appear in the audit log line.
    assert "t1" not in res.stderr
    assert "access_token" not in res.stderr


def test_oauth_pool_index_override(home: Path) -> None:
    pool = json.dumps([{"access_token": f"t{i}"} for i in range(4)])
    res = _run_with_hostname(
        {
            "CLAUDE_CREDENTIALS_POOL": pool,
            "PO_CREDENTIALS_POOL_INDEX": "3",
            "PO_BACKEND": "cli",
        },
        home,
        hostname="worker-99",  # would otherwise hash to something else
    )
    assert res.returncode == 0, res.stderr
    cred = json.loads((home / ".claude" / ".credentials.json").read_text())
    assert cred == {"access_token": "t3"}
    assert "pool index=3 size=4" in res.stderr


def test_single_credentials_beats_pool(home: Path) -> None:
    """CLAUDE_CREDENTIALS env wins over CLAUDE_CREDENTIALS_POOL."""
    single = '{"access_token": "single"}'
    pool = json.dumps([{"access_token": f"t{i}"} for i in range(3)])
    res = _run_with_hostname(
        {
            "CLAUDE_CREDENTIALS": single,
            "CLAUDE_CREDENTIALS_POOL": pool,
            "PO_BACKEND": "cli",
        },
        home,
        hostname="worker-1",
    )
    assert res.returncode == 0, res.stderr
    assert (home / ".claude" / ".credentials.json").read_text() == single
    # Pool was ignored — no pool_index in audit log.
    assert "pool index=" not in res.stderr
    # Both pool envs scrubbed.
    assert "CLAUDE_CREDENTIALS_POOL=" not in res.stdout
    assert "CLAUDE_CREDENTIALS=" not in res.stdout


def test_apikey_pool_picks_slot(home: Path) -> None:
    pool = json.dumps([f"sk-{c * 20}" for c in "abcde"])
    res = _run_with_hostname(
        {"ANTHROPIC_API_KEY_POOL": pool, "PO_BACKEND": "cli"},
        home,
        hostname="worker-2",
    )
    assert res.returncode == 0, res.stderr
    # 2 % 5 == 2 → "sk-cccccccccccccccccccc" (last 20 chars logged in approval)
    cfg = json.loads((home / ".claude.json").read_text())
    expected = "sk-" + ("c" * 20)
    assert cfg["customApiKeyResponses"]["approved"] == [expected[-20:]]
    assert "pool index=2 size=5" in res.stderr
    assert "ANTHROPIC_API_KEY_POOL=" not in res.stdout
    # The chosen api key value DOES appear in env (worker needs it). Make
    # sure the pool blob (other keys) was scrubbed.
    assert "aaaaaaaaaaaaaaaaaaaa" not in res.stdout
    assert "bbbbbbbbbbbbbbbbbbbb" not in res.stdout


def test_apikey_pool_index_override(home: Path) -> None:
    pool = json.dumps([f"sk-{i * 20}-end" for i in range(3)])
    res = _run_with_hostname(
        {
            "ANTHROPIC_API_KEY_POOL": pool,
            "PO_API_KEY_POOL_INDEX": "0",
            "PO_BACKEND": "cli",
        },
        home,
        hostname="worker-99",
    )
    assert res.returncode == 0, res.stderr
    assert "pool index=0 size=3" in res.stderr


def test_single_apikey_beats_apikey_pool(home: Path) -> None:
    pool = json.dumps([f"sk-pool-{i}" for i in range(3)])
    res = _run_with_hostname(
        {
            "ANTHROPIC_API_KEY": "sk-single-1234567890abcdef",
            "ANTHROPIC_API_KEY_POOL": pool,
            "PO_BACKEND": "cli",
        },
        home,
        hostname="worker-2",
    )
    assert res.returncode == 0, res.stderr
    assert "pool index=" not in res.stderr
    assert "ANTHROPIC_API_KEY_POOL=" not in res.stdout


def test_pool_invalid_json_exits_64(home: Path) -> None:
    res = _run_with_hostname(
        {"CLAUDE_CREDENTIALS_POOL": "not-valid-json", "PO_BACKEND": "cli"},
        home,
        hostname="worker-0",
    )
    assert res.returncode == 64
    assert "invalid CLAUDE_CREDENTIALS_POOL JSON" in res.stderr


def test_pool_empty_array_exits_64(home: Path) -> None:
    res = _run_with_hostname(
        {"CLAUDE_CREDENTIALS_POOL": "[]", "PO_BACKEND": "cli"},
        home,
        hostname="worker-0",
    )
    assert res.returncode == 64


def test_pool_index_out_of_range_exits_64(home: Path) -> None:
    pool = json.dumps([{"access_token": "a"}, {"access_token": "b"}])
    res = _run_with_hostname(
        {
            "CLAUDE_CREDENTIALS_POOL": pool,
            "PO_CREDENTIALS_POOL_INDEX": "5",
            "PO_BACKEND": "cli",
        },
        home,
        hostname="worker-0",
    )
    assert res.returncode == 64
    assert "out of range" in res.stderr


def test_pool_ordinal_distribution_is_perfect(tmp_path: Path) -> None:
    """100 ordinal hostnames `worker-0`..`worker-99` with poolSize=10
    must give exactly 10 replicas per slot — the ordinal fast path."""
    pool = json.dumps([{"access_token": f"t{i}"} for i in range(10)])
    counts: Counter[int] = Counter()
    for n in range(100):
        home_n = tmp_path / f"home-{n}"
        home_n.mkdir()
        res = _run_with_hostname(
            {"CLAUDE_CREDENTIALS_POOL": pool, "PO_BACKEND": "cli"},
            home_n,
            hostname=f"worker-{n}",
        )
        assert res.returncode == 0, res.stderr
        # Extract the chosen index from stderr.
        for line in res.stderr.splitlines():
            if "pool index=" in line:
                idx = int(line.split("pool index=")[1].split()[0])
                counts[idx] += 1
                break
    # Perfect 10-each spread.
    assert sum(counts.values()) == 100
    assert all(counts[i] == 10 for i in range(10)), counts


def test_pool_hash_distribution_is_reasonable(tmp_path: Path) -> None:
    """Random (non-ordinal) hostnames hashed mod 10: every bucket hit,
    no bucket more than 2× the expected mean (10)."""
    pool = json.dumps([{"access_token": f"t{i}"} for i in range(10)])
    counts: Counter[int] = Counter()
    # 100 random-ish hostnames (sha-mod path, not ordinal).
    for n in range(100):
        home_n = tmp_path / f"home-{n}"
        home_n.mkdir()
        res = _run_with_hostname(
            {"CLAUDE_CREDENTIALS_POOL": pool, "PO_BACKEND": "cli"},
            home_n,
            hostname=f"po-worker-{n:05d}-deadbeef",
        )
        assert res.returncode == 0, res.stderr
        for line in res.stderr.splitlines():
            if "pool index=" in line:
                idx = int(line.split("pool index=")[1].split()[0])
                counts[idx] += 1
                break
    assert sum(counts.values()) == 100
    assert len(counts) == 10, f"some buckets missed: {counts}"
    assert max(counts.values()) <= 20, f"distribution too skewed: {counts}"


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
