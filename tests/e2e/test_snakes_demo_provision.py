"""E2E tests for scripts/snakes-demo/provision-rig.sh.

This is in tests/e2e/ (not unit) because it subprocesses the real bash
script, which in turn shells out to real `git` and `bd`. Per the repo's
CLAUDE.md test-layer rules: unit tests must not invoke real subprocesses.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "snakes-demo" / "provision-rig.sh"
LANGUAGES_SRC = REPO_ROOT / "scripts" / "snakes-demo" / "languages.txt"


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bd") is None,
    reason="git and bd must be on PATH",
)


def _run(rig_path: Path, *args: str, force_failure_ok: bool = False) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "RIG_PATH": str(rig_path),
        "GIT_AUTHOR_NAME": "Test User",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        # Avoid leaking the developer's HOME-level git hooks/templates.
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    proc = subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        check=not force_failure_ok,
    )
    return proc


def test_canonical_languages_list_is_100_lines_verbatim_from_epic() -> None:
    """The list is load-bearing for slot-N -> language across 5wk.5."""
    lines = [
        line.split("\t", 1)[1].strip()
        for line in LANGUAGES_SRC.read_text().splitlines()
        if line and not line.startswith("#")
    ]
    assert len(lines) == 100, f"expected 100 entries, got {len(lines)}"
    assert lines[0] == "Python"
    assert lines[1] == "Rust"
    assert lines[2] == "Go"
    assert lines[3] == "TypeScript"
    assert lines[29] == "Bash"
    assert lines[99] == "Logo"


def test_provisions_clean_rig(tmp_path: Path) -> None:
    rig = tmp_path / "rig"
    proc = _run(rig)
    assert proc.returncode == 0, proc.stderr

    assert (rig / ".git").is_dir()
    assert (rig / ".beads").is_dir()
    assert (rig / "README.md").is_file()
    assert (rig / "CLAUDE.md").is_file()
    assert (rig / "engdocs" / "languages.txt").is_file()
    assert not (rig / "snakes").exists()

    # languages.txt has 100 non-empty, non-comment lines.
    langs = [
        line for line in (rig / "engdocs" / "languages.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert len(langs) == 100

    # CLAUDE.md carries the load-bearing instruction.
    assert "implementing the game Snake" in (rig / "CLAUDE.md").read_text()

    # Initial commit on main exists.
    log = subprocess.run(
        ["git", "-C", str(rig), "log", "--oneline"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "Initial snakes-demo rig" in log


def test_idempotent_without_force_refuses(tmp_path: Path) -> None:
    rig = tmp_path / "rig"
    _run(rig)  # first run OK

    proc = _run(rig, force_failure_ok=True)
    assert proc.returncode != 0
    assert "already exists" in proc.stderr


def test_force_wipes_and_recreates(tmp_path: Path) -> None:
    rig = tmp_path / "rig"
    _run(rig)
    # Plant a sentinel that should be wiped.
    (rig / "sentinel").write_text("gone")
    _run(rig, "--force")
    assert not (rig / "sentinel").exists()
    assert (rig / ".beads").is_dir()


def test_refuses_existing_non_beads_dir_even_with_force(tmp_path: Path) -> None:
    rig = tmp_path / "notrig"
    rig.mkdir()
    (rig / "user-data").write_text("important")

    proc = _run(rig, "--force", force_failure_ok=True)
    assert proc.returncode != 0
    assert "not a snakes-demo rig" in proc.stderr
    assert (rig / "user-data").read_text() == "important"


def test_remote_flag_adds_origin(tmp_path: Path) -> None:
    rig = tmp_path / "rig"
    _run(rig, "--remote", "git@example.com:foo/bar.git")
    remotes = subprocess.run(
        ["git", "-C", str(rig), "remote", "-v"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "git@example.com:foo/bar.git" in remotes


def test_unknown_arg_exits_2(tmp_path: Path) -> None:
    rig = tmp_path / "rig"
    proc = _run(rig, "--bogus", force_failure_ok=True)
    assert proc.returncode == 2
    assert "unknown argument" in proc.stderr


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck_strict_passes() -> None:
    proc = subprocess.run(
        ["shellcheck", "-S", "style", str(SCRIPT)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
