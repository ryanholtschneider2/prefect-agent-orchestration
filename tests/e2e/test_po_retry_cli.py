"""E2E tests for the `po retry` CLI.

Exercises the Typer → retry module wiring by invoking the real `po`
binary in a subprocess. Does not talk to a live Prefect server or `bd`
— it relies only on the shape of `--help` output and the documented
non-zero exits for unresolvable beads.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _po(
    *args: str, env_overrides: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    po_bin = REPO_ROOT / ".venv" / "bin" / "po"
    if not po_bin.exists():
        pytest.skip(f"po CLI not installed at {po_bin}; run `uv sync` first")
    env = os.environ.copy()
    env["PREFECT_API_URL"] = "http://127.0.0.1:1/api"
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [str(po_bin), *args],
        cwd=tempfile.mkdtemp(prefix="po-e2e-"),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_po_retry_listed_in_help() -> None:
    result = _po("--help")
    assert result.returncode == 0, result.stderr
    assert "retry" in result.stdout


def test_po_retry_help_flags() -> None:
    result = _po("retry", "--help")
    assert result.returncode == 0, result.stderr
    for flag in ("--keep-sessions", "--rig", "--force", "--formula"):
        assert flag in result.stdout, f"missing {flag} in `po retry --help`"


def test_po_retry_unknown_issue_exits_nonzero() -> None:
    """An issue with no recorded metadata → RunDirNotFound → exit 2."""
    # Use an id that almost certainly has no bd metadata.
    result = _po(
        "retry",
        "definitely-not-a-real-issue-zzzzz",
        env_overrides={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode != 0
    # Shouldn't dump a traceback at the user.
    assert "Traceback" not in result.stderr
