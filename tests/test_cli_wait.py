"""Unit tests for the `po wait` Typer subcommand.

Stubs `_bd_show` + `_bd_available` from `beads_meta` and `time.sleep` so
the loop runs in milliseconds. Covers the four documented exit codes
(0/1/2/3) plus the `--any` variant.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from prefect_orchestration import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _patch_wait(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows_per_call: list[dict[str, dict | None]],
    bd_available: bool = True,
) -> None:
    """Replace `_bd_show` with a scripted sequence of (id → row) maps,
    advancing one map per outer poll-tick. `time.sleep` is also stubbed
    to a no-op so the test runs synchronously."""
    from prefect_orchestration import beads_meta

    state = {"tick": 0}

    def fake_show(issue_id: str, rig_path: Any = None) -> dict | None:
        idx = min(state["tick"], len(rows_per_call) - 1)
        return rows_per_call[idx].get(issue_id)

    def advance_tick(_seconds: float) -> None:
        state["tick"] += 1

    monkeypatch.setattr(beads_meta, "_bd_show", fake_show)
    monkeypatch.setattr(beads_meta, "_bd_available", lambda: bd_available)
    monkeypatch.setattr("time.sleep", advance_tick)


# ─── exit 0: clean close ─────────────────────────────────────────────


def test_wait_exits_zero_when_all_closed_cleanly(monkeypatch, runner):
    _patch_wait(monkeypatch, rows_per_call=[
        {"a": {"status": "closed", "close_reason": "po simple-mode complete"},
         "b": {"status": "closed", "close_reason": "no regression: 763 passed"}},
    ])
    result = runner.invoke(cli.app, ["wait", "a", "b", "--poll", "1", "--quiet"])
    assert result.exit_code == 0, result.output


# ─── exit 1: failure-coded reason ────────────────────────────────────


def test_wait_exits_one_on_failure_coded_reason(monkeypatch, runner):
    _patch_wait(monkeypatch, rows_per_call=[
        {"a": {"status": "closed", "close_reason": "po simple-mode complete"},
         "b": {"status": "closed",
               "close_reason": "cap-exhausted: too many critic iters"}},
    ])
    result = runner.invoke(cli.app, ["wait", "a", "b", "--poll", "1", "--quiet"])
    assert result.exit_code == 1, result.output
    assert "failure-coded" in result.output or "failure-coded" in (result.stderr or "")


def test_wait_failure_marker_matches_prefix(monkeypatch, runner):
    """Verdict keywords like `rejected:` / `failed:` are matched as
    prefixes (after lstrip), not anywhere — keeps "no regression:"
    from false-firing on the `regression:` failure marker."""
    _patch_wait(monkeypatch, rows_per_call=[
        {"a": {"status": "closed", "close_reason": "rejected: ACs not met"}},
    ])
    result = runner.invoke(cli.app, ["wait", "a", "--poll", "1", "--quiet"])
    assert result.exit_code == 1, result.output


def test_wait_no_regression_is_success(monkeypatch, runner):
    """Regression-gate's `no regression:` close should NOT match the
    failure-prefix `regression:`. Regression bug we caught: the original
    matcher used substring containment."""
    _patch_wait(monkeypatch, rows_per_call=[
        {"a": {"status": "closed", "close_reason": "no regression: 763 passed"}},
    ])
    result = runner.invoke(cli.app, ["wait", "a", "--poll", "1", "--quiet"])
    assert result.exit_code == 0, result.output


# ─── exit 2: timeout ─────────────────────────────────────────────────


def test_wait_exits_two_on_timeout(monkeypatch, runner):
    """Issue stays open across all polls → timeout fires."""
    _patch_wait(monkeypatch, rows_per_call=[
        {"a": {"status": "open", "close_reason": ""}},
    ])
    result = runner.invoke(
        cli.app, ["wait", "a", "--timeout", "1", "--poll", "1", "--quiet"]
    )
    assert result.exit_code == 2, result.output


# ─── exit 3: bd unavailable / unknown id ─────────────────────────────


def test_wait_exits_three_when_bd_missing(monkeypatch, runner):
    _patch_wait(monkeypatch, rows_per_call=[{}], bd_available=False)
    result = runner.invoke(cli.app, ["wait", "a", "--poll", "1", "--quiet"])
    assert result.exit_code == 3, result.output


def test_wait_exits_three_when_id_not_found(monkeypatch, runner):
    _patch_wait(monkeypatch, rows_per_call=[{"a": None}])
    result = runner.invoke(cli.app, ["wait", "a", "--poll", "1", "--quiet"])
    assert result.exit_code == 3, result.output


# ─── --any: first close wins ─────────────────────────────────────────


def test_wait_any_returns_when_first_closes(monkeypatch, runner):
    """First issue closes on tick 0; second is still open. `--any`
    should return immediately with exit 0."""
    _patch_wait(monkeypatch, rows_per_call=[
        {"a": {"status": "closed", "close_reason": "po simple-mode complete"},
         "b": {"status": "open", "close_reason": ""}},
    ])
    result = runner.invoke(
        cli.app, ["wait", "a", "b", "--any", "--poll", "1", "--quiet"]
    )
    assert result.exit_code == 0, result.output
