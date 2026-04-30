"""E2E argv-shape regressions for `po run --at` (issue prefect-orchestration-40y).

Invokes the real installed `po` script in a subprocess. Server-backed
scheduling is intentionally **not** exercised here — registering a real
`<formula>-manual` deployment requires touching the software-dev pack
repo, which is out of scope per the rig boundary. Unit tests in
`tests/test_cli_run_time.py` cover the happy path with mocks.

These are the things this layer alone can catch:
  * the `--at` flag is wired into `--help`; `--time` is hidden
  * `po run nonexistent --at 2h` exits non-zero cleanly without a
    Python traceback (regression — adding `--at` must not break the
    unknown-formula path)
  * `--time` still exits without traceback and shows a deprecation warning
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
    env.pop("PREFECT_API_URL", None)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [str(po_bin), *args],
        cwd=tempfile.mkdtemp(prefix="po-e2e-time-"),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_po_run_help_advertises_at_flag() -> None:
    """--at is in help; --time is hidden (not shown)."""
    result = _po("run", "--help")
    assert result.returncode == 0, result.stderr
    assert "--at" in result.stdout
    assert "--time" not in result.stdout
    # Help text mentions the deployment-name convention so users find the docs:
    combined = result.stdout.lower()
    assert "manual" in combined


def test_po_run_unknown_formula_with_at_fails_cleanly() -> None:
    """Adding --at must not crash the unknown-formula path."""
    result = _po("run", "nonexistent-formula", "--at", "2h")
    assert result.returncode != 0
    # Clean error, no traceback
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout
    combined = (result.stdout + result.stderr).lower()
    assert "no formula named" in combined or "formula" in combined


def test_po_run_time_alias_shows_deprecation_warning() -> None:
    """`--time` still exits non-zero cleanly and prints a deprecation warning."""
    result = _po("run", "nonexistent-formula", "--time", "2h")
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout
    combined = result.stdout + result.stderr
    assert "deprecated" in combined


def test_po_run_garbage_time_fails_with_clean_error() -> None:
    """Bad --at spec is caught before any Prefect call. We need a
    registered formula to reach the parse step, so we expect either the
    parse error OR a missing-formula error — both are clean exits."""
    result = _po("run", "nonexistent-formula", "--at", "yesterday")
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout
