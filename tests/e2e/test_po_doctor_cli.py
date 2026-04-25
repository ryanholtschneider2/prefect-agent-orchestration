"""E2E tests for the `po doctor` CLI.

Invokes the real installed `po` script in a subprocess, exercising the
full path: Typer app → doctor module → real `importlib.metadata` +
`subprocess` probes. No mocking — whatever the local env says.
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


def test_po_doctor_help_lists_command() -> None:
    result = _po("--help")
    assert result.returncode == 0, result.stderr
    assert "doctor" in result.stdout


def test_po_doctor_runs_without_crashing() -> None:
    """`po doctor` must always produce a report — exit 0 or 1, never traceback."""
    result = _po("doctor")
    assert result.returncode in (0, 1), (
        f"unexpected exit {result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout


def test_po_doctor_output_contains_check_table() -> None:
    """Output should look like a per-check report, not an empty string."""
    result = _po("doctor")
    combined = result.stdout + result.stderr
    # At minimum the bd-on-PATH check always runs.
    lower = combined.lower()
    assert "bd" in lower, f"doctor output missing bd check:\n{combined}"


def test_po_doctor_exit_code_reflects_critical_failure() -> None:
    """With PREFECT_API_URL pointing at an unreachable host, doctor should
    flag a critical failure and exit 1 (Prefect unreachable is critical)."""
    result = _po(
        "doctor",
        env_overrides={"PREFECT_API_URL": "http://127.0.0.1:1/api"},
    )
    # unreachable Prefect → critical → exit 1
    assert result.returncode == 1, (
        f"expected exit 1 for unreachable Prefect, got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "Traceback" not in result.stderr
