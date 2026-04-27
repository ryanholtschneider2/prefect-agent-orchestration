"""Regression tests for prefect-orchestration-sul.

`_ensure_stop_hook` must write the Stop hook to `.claude/settings.local.json`,
NOT the committed `.claude/settings.json`. The committed file is vulnerable
to `git restore` / `git checkout HEAD --` (invoked anywhere in the rig —
agent cleanup, build steps, CI), which silently strips our Stop entry and
wedges the whole flow: claude TUIs spawn without Stop, no <sid>.stopped
sentinel ever lands in ~/.cache/po-stops/, and `_discover_resumed_sentinel`
polls forever.

settings.local.json is globally gitignored, so nothing in a normal git
workflow touches it. Claude Code merges both files at load time, so the
hook still fires.
"""

from __future__ import annotations

import json
from pathlib import Path

from prefect_orchestration.agent_session import _ensure_stop_hook


def test_writes_stop_hook_to_settings_local_not_committed(tmp_path: Path) -> None:
    """The hook lands in settings.local.json; settings.json is untouched."""
    _ensure_stop_hook(tmp_path)

    local = tmp_path / ".claude" / "settings.local.json"
    committed = tmp_path / ".claude" / "settings.json"

    assert local.exists()
    assert not committed.exists()  # we never write here

    data = json.loads(local.read_text())
    assert "Stop" in data["hooks"]
    stop_cmd = data["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert "prefect_orchestration.stop_hook" in stop_cmd


def test_does_not_clobber_existing_local_settings(tmp_path: Path) -> None:
    """Pre-existing settings.local.json content (e.g. permissions allowlist)
    is preserved when we add the Stop hook."""
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    local = settings_dir / "settings.local.json"
    local.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}))

    _ensure_stop_hook(tmp_path)

    data = json.loads(local.read_text())
    assert data["permissions"]["allow"] == ["Bash(ls)"]
    assert "Stop" in data["hooks"]


def test_does_not_modify_committed_settings_when_present(tmp_path: Path) -> None:
    """If a committed settings.json exists alongside, we never touch it.

    This is the regression: prior code wrote Stop into settings.json; a
    subsequent `git restore` reverted it, dropping the hook silently.
    """
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    committed = settings_dir / "settings.json"
    committed_body = json.dumps(
        {"hooks": {"PreCompact": [{"hooks": [{"type": "command", "command": "x"}]}]}}
    )
    committed.write_text(committed_body)

    _ensure_stop_hook(tmp_path)

    # Committed file is byte-for-byte unchanged.
    assert committed.read_text() == committed_body


def test_idempotent_no_rewrite_when_already_configured(tmp_path: Path) -> None:
    """Second call with same hook command is a no-op (skip-write path)."""
    _ensure_stop_hook(tmp_path)
    local = tmp_path / ".claude" / "settings.local.json"
    first_mtime = local.stat().st_mtime_ns

    # Second call should hit the early-return without rewriting.
    _ensure_stop_hook(tmp_path)
    assert local.stat().st_mtime_ns == first_mtime


def test_handles_corrupt_local_settings(tmp_path: Path) -> None:
    """If settings.local.json is malformed JSON (mid-write race), we
    overwrite cleanly rather than crashing the spawn."""
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.local.json").write_text("{not-json")

    _ensure_stop_hook(tmp_path)

    data = json.loads((settings_dir / "settings.local.json").read_text())
    assert "Stop" in data["hooks"]
