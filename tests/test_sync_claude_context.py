"""Unit tests for scripts/sync-claude-context.sh.

Covers AC #4 of prefect-orchestration-tyf.2: the bootstrap script must
package local `~/.claude` minus credentials/projects/cache, and must
sanitize settings.json (drop hooks, mcpServers, secret-like keys).

The script is bash, so we shell out and assert on the resulting tree
rather than importing Python. Skipped if rsync or jq aren't available
(they're declared deps, but CI environments vary).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "sync-claude-context.sh"


def _have(*tools: str) -> bool:
    return all(shutil.which(t) is not None for t in tools)


pytestmark = pytest.mark.skipif(
    not _have("bash", "rsync", "jq"),
    reason="sync script needs bash + rsync + jq on PATH",
)


def _build_fixture_src(tmp_path: Path) -> Path:
    """Build a fake ~/.claude with planted secrets and good content."""
    src = tmp_path / "claude-src"
    src.mkdir()

    # Whitelisted content — should survive.
    (src / "CLAUDE.md").write_text("# user instructions\nbe helpful\n")
    (src / "prompts").mkdir()
    (src / "prompts" / "fastapi.md").write_text("fastapi notes\n")
    (src / "skills").mkdir()
    (src / "skills" / "po").mkdir()
    (src / "skills" / "po" / "SKILL.md").write_text("po skill body\n")
    (src / "commands").mkdir()
    (src / "commands" / "review.md").write_text("review command\n")

    # Sensitive top-level files/dirs — must NOT survive.
    (src / ".credentials.json").write_text('{"refresh_token":"sk-FAKE-CRED-12345678901234567890"}')
    (src / "projects").mkdir()
    (src / "projects" / "history.jsonl").write_text("session log\n")
    (src / "history.jsonl").write_text("global history\n")
    (src / "cache").mkdir()
    (src / "cache" / "blob").write_text("cached")
    (src / "secrets").mkdir()
    (src / "secrets" / "leak").write_text("ghp_FAKE0000000000000000000000000000")
    (src / "session-env").mkdir()
    (src / "session-env" / "x").write_text("env")
    (src / "hooks").mkdir()
    (src / "hooks" / "pre.sh").write_text("#!/bin/sh\n")

    # settings.json with sensitive keys that must be stripped.
    (src / "settings.json").write_text(
        json.dumps(
            {
                "$schema": "https://json.schemastore.org/claude-code-settings.json",
                "theme": "dark",
                "model": "opus",
                "hooks": {"pre": "/Users/x/hook.sh"},
                "mcpServers": {
                    "rube": {"url": "https://x", "token": "sk-FAKE-MCP-12345678901234567890"}
                },
                "anthropicApiKey": "sk-FAKE-MCP-12345678901234567890",
                "secretToken": "ghp_FAKE0000000000000000000000000000",
            }
        )
    )
    return src


def _run_sync(src: Path, dest: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), "--force", *extra],
        env={
            "SRC": str(src),
            "DEST": str(dest),
            "PATH": __import__("os").environ.get("PATH", ""),
            "HOME": __import__("os").environ.get("HOME", ""),
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_whitelist_survives(tmp_path: Path) -> None:
    src = _build_fixture_src(tmp_path)
    dest = tmp_path / "out"
    res = _run_sync(src, dest)
    assert res.returncode == 0, f"stdout={res.stdout!r} stderr={res.stderr!r}"
    assert (dest / "CLAUDE.md").read_text().startswith("# user instructions")
    assert (dest / "prompts" / "fastapi.md").is_file()
    assert (dest / "skills" / "po" / "SKILL.md").is_file()
    assert (dest / "commands" / "review.md").is_file()
    assert (dest / "settings.json").is_file()


def test_blacklist_refused(tmp_path: Path) -> None:
    src = _build_fixture_src(tmp_path)
    dest = tmp_path / "out"
    res = _run_sync(src, dest)
    assert res.returncode == 0, f"stderr={res.stderr!r}"
    # None of these top-level dirs/files should make it across.
    for name in (
        ".credentials.json",
        "projects",
        "history.jsonl",
        "cache",
        "secrets",
        "session-env",
        "hooks",
    ):
        assert not (dest / name).exists(), f"{name} leaked into {dest}"


def test_settings_sanitization(tmp_path: Path) -> None:
    src = _build_fixture_src(tmp_path)
    dest = tmp_path / "out"
    res = _run_sync(src, dest)
    assert res.returncode == 0, f"stderr={res.stderr!r}"
    settings = json.loads((dest / "settings.json").read_text())
    # Whitelisted keys retained.
    assert settings.get("theme") == "dark"
    assert settings.get("model") == "opus"
    # Stripped keys: hooks (top-level allowlist), mcpServers (not in allowlist),
    # anthropicApiKey/secretToken (substring filter).
    assert "hooks" not in settings
    assert "mcpServers" not in settings
    assert "anthropicApiKey" not in settings
    assert "secretToken" not in settings
    # And no secret-like string survived in the rendered text.
    raw = (dest / "settings.json").read_text()
    assert "sk-FAKE-MCP-" not in raw
    assert "ghp_FAKE" not in raw


def test_idempotent_with_force(tmp_path: Path) -> None:
    src = _build_fixture_src(tmp_path)
    dest = tmp_path / "out"
    assert _run_sync(src, dest).returncode == 0
    # Second run with --force should converge cleanly, no error.
    res2 = _run_sync(src, dest)
    assert res2.returncode == 0, f"second run failed: {res2.stderr!r}"
    assert (dest / "CLAUDE.md").is_file()


def test_refuses_nonempty_dest_without_force(tmp_path: Path) -> None:
    src = _build_fixture_src(tmp_path)
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "stale.txt").write_text("stale")
    res = subprocess.run(
        ["bash", str(SCRIPT)],  # no --force
        env={
            "SRC": str(src),
            "DEST": str(dest),
            "PATH": __import__("os").environ.get("PATH", ""),
            "HOME": __import__("os").environ.get("HOME", ""),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert "not empty" in res.stderr.lower() or "force" in res.stderr.lower()


def test_missing_source_dir_fails(tmp_path: Path) -> None:
    res = _run_sync(tmp_path / "does-not-exist", tmp_path / "out")
    assert res.returncode != 0
    assert "not a directory" in res.stderr.lower()


def test_emit_configmap_smoke(tmp_path: Path) -> None:
    if not _have("kubectl"):
        pytest.skip("kubectl not on PATH")
    src = _build_fixture_src(tmp_path)
    dest = tmp_path / "out"
    cm_path = tmp_path / "configmap.yaml"
    res = _run_sync(src, dest, "--emit-configmap", str(cm_path))
    assert res.returncode == 0, f"stderr={res.stderr!r}"
    assert cm_path.is_file()
    body = cm_path.read_text()
    assert "kind: ConfigMap" in body
    assert "CLAUDE.md" in body
    assert "settings.json" in body
    # No leaked secret content.
    assert "sk-FAKE" not in body
    assert "ghp_FAKE" not in body
