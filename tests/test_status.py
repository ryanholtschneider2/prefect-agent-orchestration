"""Unit tests for `prefect_orchestration.status` and `po status` CLI.

No live Prefect server — everything runs against simple fakes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from typer.testing import CliRunner

from prefect_orchestration import status as _status
from prefect_orchestration.cli import app


# ─── fake Prefect objects ────────────────────────────────────────────


@dataclass
class FakeState:
    type: str
    name: str


@dataclass
class FakeTaskRun:
    name: str
    state_type: str
    start_time: datetime | None = None


@dataclass
class FakeFlowRun:
    id: str
    name: str
    tags: list[str]
    state_name: str = "Running"
    expected_start_time: datetime | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    created: datetime | None = None


# ─── parse_since ─────────────────────────────────────────────────────


def test_parse_since_relative() -> None:
    now = datetime.now(timezone.utc)
    assert (
        abs((_status.parse_since("1h") - (now - timedelta(hours=1))).total_seconds())
        < 5
    )
    assert (
        abs(
            (_status.parse_since("30m") - (now - timedelta(minutes=30))).total_seconds()
        )
        < 5
    )
    assert (
        abs((_status.parse_since("2d") - (now - timedelta(days=2))).total_seconds()) < 5
    )
    assert (
        abs((_status.parse_since("1w") - (now - timedelta(weeks=1))).total_seconds())
        < 5
    )


def test_parse_since_iso8601() -> None:
    out = _status.parse_since("2026-04-01T00:00:00Z")
    assert out == datetime(2026, 4, 1, tzinfo=timezone.utc)
    # naive ISO → treated as UTC
    out2 = _status.parse_since("2026-04-01T00:00:00")
    assert out2 == datetime(2026, 4, 1, tzinfo=timezone.utc)


def test_parse_since_bad() -> None:
    with pytest.raises(ValueError):
        _status.parse_since("yesterday")
    with pytest.raises(ValueError):
        _status.parse_since("")


# ─── extract_issue_id / group_by_issue ───────────────────────────────


def test_extract_issue_id() -> None:
    assert _status.extract_issue_id(["foo", "issue_id:po-1", "bar"]) == "po-1"
    assert _status.extract_issue_id(["foo", "bar"]) is None
    assert _status.extract_issue_id([]) is None


def test_group_by_issue_picks_latest_per_issue() -> None:
    t0 = datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc)
    runs = [
        FakeFlowRun(
            id="a",
            name="software_dev_full",
            tags=["issue_id:po-1"],
            expected_start_time=t0,
        ),
        FakeFlowRun(
            id="b",
            name="software_dev_full",
            tags=["issue_id:po-1"],
            expected_start_time=t0 + timedelta(hours=1),
        ),
        FakeFlowRun(
            id="c", name="epic_run", tags=["issue_id:po-2"], expected_start_time=t0
        ),
        FakeFlowRun(id="d", name="other", tags=["misc"], expected_start_time=t0),
    ]
    groups = _status.group_by_issue(runs)
    by_issue = {g.issue_id: g for g in groups}
    assert set(by_issue) == {"po-1", "po-2"}
    assert by_issue["po-1"].latest.id == "b"
    assert by_issue["po-1"].extras[0].id == "a"
    assert by_issue["po-2"].extra_count == 0


def test_group_by_issue_empty() -> None:
    assert _status.group_by_issue([]) == []


# ─── current_step ────────────────────────────────────────────────────


def test_current_step_prefers_non_terminal() -> None:
    t0 = datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc)
    trs = [
        FakeTaskRun(name="triage", state_type="COMPLETED", start_time=t0),
        FakeTaskRun(
            name="plan", state_type="COMPLETED", start_time=t0 + timedelta(minutes=1)
        ),
        FakeTaskRun(
            name="build", state_type="RUNNING", start_time=t0 + timedelta(minutes=2)
        ),
    ]
    assert _status.current_step(trs) == "build"


def test_current_step_all_terminal_returns_latest() -> None:
    t0 = datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc)
    trs = [
        FakeTaskRun(name="triage", state_type="COMPLETED", start_time=t0),
        FakeTaskRun(
            name="learn", state_type="COMPLETED", start_time=t0 + timedelta(minutes=5)
        ),
    ]
    assert _status.current_step(trs) == "learn"


def test_current_step_empty() -> None:
    assert _status.current_step([]) is None


# ─── render_table ────────────────────────────────────────────────────


def test_render_table_row_per_issue() -> None:
    t0 = datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc)
    g = _status.IssueGroup(
        issue_id="po-dmy",
        latest=FakeFlowRun(
            id="x",
            name="software_dev_full/abc",
            tags=["issue_id:po-dmy"],
            state_name="Running",
            start_time=t0,
        ),
        extras=[],
        current_step="build",
    )
    out = _status.render_table([g])
    assert "ISSUE" in out and "STATE" in out and "STEP" in out
    assert "po-dmy" in out
    assert "Running" in out
    assert "build" in out


def test_render_table_empty() -> None:
    assert "no flow runs" in _status.render_table([])


# ─── find_runs_by_issue_id ───────────────────────────────────────────


class _FakeClient:
    def __init__(self, runs: list[Any]) -> None:
        self.runs = runs
        self.call_kwargs: dict[str, Any] = {}

    async def read_flow_runs(self, **kwargs: Any) -> list[Any]:
        self.call_kwargs = kwargs
        return list(self.runs)


@pytest.mark.asyncio
async def test_find_runs_drops_untagged_when_no_issue_filter() -> None:
    runs = [
        FakeFlowRun(id="a", name="f", tags=["issue_id:po-1"]),
        FakeFlowRun(id="b", name="f", tags=["random"]),
    ]
    client = _FakeClient(runs)
    out = await _status.find_runs_by_issue_id(client)
    ids = [r.id for r in out]
    assert ids == ["a"]


@pytest.mark.asyncio
async def test_find_runs_applies_tag_filter_server_side() -> None:
    client = _FakeClient([FakeFlowRun(id="a", name="f", tags=["issue_id:po-9"])])
    await _status.find_runs_by_issue_id(client, issue_id="po-9")
    flt = client.call_kwargs["flow_run_filter"]
    # Server-side tag filter is set to exactly `issue_id:po-9`
    assert any("po-9" in t for t in flt.tags.all_)


# ─── CLI: exit 0 on server down (AC3) ────────────────────────────────


def test_status_cli_exits_zero_when_server_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC3: `po status` must exit 0 even when the Prefect server is unreachable."""

    from prefect_orchestration import cli as _cli

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("nope")

    monkeypatch.setattr("prefect.client.orchestration.get_client", _boom)

    runner = CliRunner()
    result = runner.invoke(_cli.app, ["status"])
    assert result.exit_code == 0, result.stderr + result.stdout
    assert "error:" in (result.stderr if result.stderr else result.output)


def test_status_cli_bad_since_exits_zero() -> None:
    """`--since` garbage is also treated as observation → exit 0, error to stderr."""
    runner = CliRunner()
    result = runner.invoke(app, ["status", "--since", "yesterday"])
    assert result.exit_code == 0
    assert "error:" in (result.stderr if result.stderr else result.output)
