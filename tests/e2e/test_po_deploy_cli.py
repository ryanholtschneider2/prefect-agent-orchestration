"""E2E tests for the `po deploy` CLI.

Invokes the real installed `po` script in a subprocess, exercising the
full path: Typer app → real `importlib.metadata.entry_points` →
deployments module. No entry-point stubbing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _po(*args: str, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Invoke the installed `po` CLI via the project venv."""
    po_bin = REPO_ROOT / ".venv" / "bin" / "po"
    if not po_bin.exists():
        pytest.skip(f"po CLI not installed at {po_bin}; run `uv sync` first")
    env = os.environ.copy()
    # Drop any inherited PREFECT_API_URL for deterministic --apply guard tests
    env.pop("PREFECT_API_URL", None)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [str(po_bin), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_po_deploy_help_exposes_flags() -> None:
    result = _po("deploy", "--help")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for flag in ("--apply", "--pack", "--name", "--work-pool"):
        assert flag in out, f"{flag} missing from --help output"


def test_po_deploy_list_with_no_packs_is_clean() -> None:
    """With no `po.deployments` packs installed, listing exits 0 cleanly."""
    result = _po("deploy")
    assert result.returncode == 0, result.stderr
    # No registered packs → no deployments — message should indicate emptiness,
    # not crash. We assert a non-error exit + no traceback.
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout


def test_po_deploy_apply_without_api_url_exits_nonzero() -> None:
    """`--apply` must refuse when PREFECT_API_URL is unset (decision log §--apply)."""
    result = _po("deploy", "--apply")
    assert result.returncode != 0
    combined = (result.stdout + result.stderr).lower()
    assert "prefect_api_url" in combined or "api url" in combined
    assert "Traceback" not in result.stderr


def test_po_top_level_help_lists_deploy_command() -> None:
    result = _po("--help")
    assert result.returncode == 0, result.stderr
    assert "deploy" in result.stdout


def test_po_run_still_works_unchanged() -> None:
    """Acceptance criterion (5): existing `po run` still works.

    No formulas are registered in core, so `po list` should exit 0 with no
    formulas, and `po run nonexistent` should error cleanly (not crash).
    """
    result_list = _po("list")
    assert result_list.returncode == 0, result_list.stderr
    assert "Traceback" not in result_list.stderr

    result_run = _po("run", "nonexistent-formula")
    # Expected to fail with a typer error, not a traceback from core.
    assert result_run.returncode != 0
    assert "Traceback" not in result_run.stderr
