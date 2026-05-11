"""Unit tests for the --dry-run / --stub-backend CLI flag split (prefect-orchestration-58p)."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from prefect_orchestration.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _patch_prefect_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prefect_orchestration.cli._autoconfigure_prefect_api", lambda: None
    )


@pytest.fixture(autouse=True)
def _clean_po_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PO_BACKEND", raising=False)


def test_dry_run_exits_without_flow_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """--dry-run must not call the formula at all."""
    mock_flow = MagicMock(return_value="ok")
    monkeypatch.setattr(
        "prefect_orchestration.cli._load_formulas", lambda: {"fake": mock_flow}
    )
    result = runner.invoke(app, ["run", "fake", "--issue-id", "x-1", "--dry-run"])
    assert result.exit_code == 0, result.output
    mock_flow.assert_not_called()
    assert "[dry-run]" in result.output


def test_dry_run_prints_formula_and_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """--dry-run prints the formula name and parsed kwargs."""
    mock_flow = MagicMock(return_value="ok")
    monkeypatch.setattr(
        "prefect_orchestration.cli._load_formulas", lambda: {"fake": mock_flow}
    )
    result = runner.invoke(
        app, ["run", "fake", "--issue-id", "abc-1", "--rig", "myrig", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert "fake" in result.output
    assert "issue_id" in result.output
    assert "abc-1" in result.output


def test_stub_backend_sets_env_and_calls_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """--stub-backend sets PO_BACKEND=stub and still calls the formula."""
    captured_env: dict[str, Any] = {}

    def _fake_flow(**kwargs: Any) -> str:
        captured_env["PO_BACKEND"] = os.environ.get("PO_BACKEND")
        return "done"

    _fake_flow.__name__ = "_fake_flow"  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "prefect_orchestration.cli._load_formulas", lambda: {"fake": _fake_flow}
    )
    result = runner.invoke(app, ["run", "fake", "--issue-id", "x-1", "--stub-backend"])
    assert result.exit_code == 0, result.output
    assert captured_env.get("PO_BACKEND") == "stub"


def test_dry_run_and_stub_backend_are_mutually_exclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--dry-run and --stub-backend together must error with exit code 2."""
    mock_flow = MagicMock(return_value="ok")
    monkeypatch.setattr(
        "prefect_orchestration.cli._load_formulas", lambda: {"fake": mock_flow}
    )
    result = runner.invoke(
        app, ["run", "fake", "--dry-run", "--stub-backend"], catch_exceptions=False
    )
    assert result.exit_code == 2
    combined = result.output or ""
    assert "mutually exclusive" in combined
