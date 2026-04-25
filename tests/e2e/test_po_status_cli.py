"""E2E tests for the `po status` CLI.

Invokes the real installed `po` script in a subprocess, exercising
Typer → `prefect_orchestration.status` → `prefect.client` path. No
live Prefect server is required: the command is guaranteed to exit 0
even when the server is unreachable (AC3: observation, never tracebacks).
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
    # Force a bogus API URL so the test never touches a real server,
    # regardless of the developer's inherited environment.
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


def test_po_status_listed_in_help() -> None:
    result = _po("--help")
    assert result.returncode == 0, result.stderr
    assert "status" in result.stdout


def test_po_status_help_flags() -> None:
    result = _po("status", "--help")
    assert result.returncode == 0, result.stderr
    for flag in ("--issue-id", "--since", "--all", "--state", "--limit"):
        assert flag in result.stdout, f"missing {flag} in `po status --help`"


def test_po_status_bad_since_prints_error_and_exits_zero() -> None:
    """AC: `--since` parse errors go to stderr; command still exits 0."""
    result = _po("status", "--since", "not-a-duration")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "error" in result.stderr.lower()
    # No Python traceback should leak to the user.
    assert "Traceback" not in result.stderr


def test_po_status_unreachable_server_is_observational() -> None:
    """AC3: when the Prefect server is unreachable, print an error to
    stderr but still exit 0 — status is an observation, never a check.
    """
    result = _po("status", "--since", "1h")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr
    # Either a connect error (expected) or an empty table if something
    # happens to answer on port 1; both are acceptable non-crash paths.
    assert result.stderr or result.stdout
