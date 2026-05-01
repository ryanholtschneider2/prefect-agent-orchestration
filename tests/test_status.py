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
    parameters: dict[str, Any] | None = None


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


def test_render_table_shows_rig_and_run_columns() -> None:
    """`po status` should surface the rig (so users know which bd
    database the issue lives in — addresses the rig-4lp confusion)
    and a flow-run UUID prefix (so users can `prefect flow-run inspect
    <prefix>` or jump to the UI directly)."""
    t0 = datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc)
    g = _status.IssueGroup(
        issue_id="rig-4lp",
        latest=FakeFlowRun(
            id="60f43185-ca71-4589-a97a-4a0117e2cd3a",
            name="rig-4lp",
            tags=["issue_id:rig-4lp"],
            state_name="Running",
            start_time=t0,
            parameters={"rig": "uc0-e2e", "rig_path": "/path/to/uc0-e2e"},
        ),
        extras=[],
        current_step="build",
    )
    out = _status.render_table([g])
    assert "RIG" in out and "RUN" in out
    assert "uc0-e2e" in out, out
    assert "60f43185" in out, "first 8 chars of flow-run UUID should appear"


def test_render_table_rig_falls_back_to_rig_path_basename() -> None:
    """When `rig` param isn't set, derive a label from `rig_path`."""
    g = _status.IssueGroup(
        issue_id="x",
        latest=FakeFlowRun(
            id="abc-123",
            name="x",
            tags=["issue_id:x"],
            parameters={"rig_path": "/some/path/my-rig"},
        ),
        extras=[],
        current_step="-",
    )
    out = _status.render_table([g])
    assert "my-rig" in out


def test_render_table_rig_dash_when_absent() -> None:
    """Ad-hoc / scratch flows have no rig parameter — show `-`, not blank."""
    g = _status.IssueGroup(
        issue_id="ad-hoc",
        latest=FakeFlowRun(
            id="abc",
            name="ad-hoc",
            tags=["issue_id:ad-hoc"],
            parameters=None,
        ),
        extras=[],
        current_step="-",
    )
    out = _status.render_table([g])
    # Header column "RIG" + a dash row entry under it.
    assert "RIG" in out
    # Two dashes per row for unknown rig + step.
    assert " - " in out


def test_render_table_empty() -> None:
    assert "no flow runs" in _status.render_table([])


# ─── partition_zombies ───────────────────────────────────────────────


def test_partition_zombies_hides_running_with_missing_rig_path(tmp_path) -> None:
    """The classic test-leak case: a Running flow whose rig_path is a
    `/tmp/pytest-of-*` dir that's been cleaned up. Should be filtered."""
    missing = tmp_path / "deleted-by-pytest"
    # NOTE: not creating it — that's the whole point.
    g = _status.IssueGroup(
        issue_id="rig-zzz",
        latest=FakeFlowRun(
            id="x",
            name="rig-zzz",
            tags=["issue_id:rig-zzz"],
            state_name="Running",
            parameters={"rig_path": str(missing)},
        ),
        extras=[],
        current_step="-",
    )
    live, hidden = _status.partition_zombies([g])
    assert live == []
    assert hidden == 1


def test_partition_zombies_keeps_running_when_rig_path_exists(tmp_path) -> None:
    g = _status.IssueGroup(
        issue_id="alive",
        latest=FakeFlowRun(
            id="x",
            name="alive",
            tags=["issue_id:alive"],
            state_name="Running",
            parameters={"rig_path": str(tmp_path)},
        ),
        extras=[],
        current_step="build",
    )
    live, hidden = _status.partition_zombies([g])
    assert len(live) == 1
    assert hidden == 0


def test_partition_zombies_keeps_cancelled_with_missing_rig_path(tmp_path) -> None:
    """Cancelled / Completed / Failed flows are real history. We don't
    hide them just because their temp rig got cleaned up — they tell the
    truth: 'this run finished.'"""
    g = _status.IssueGroup(
        issue_id="done",
        latest=FakeFlowRun(
            id="x",
            name="done",
            tags=["issue_id:done"],
            state_name="Cancelled",
            parameters={"rig_path": str(tmp_path / "gone")},
        ),
        extras=[],
        current_step="-",
    )
    live, hidden = _status.partition_zombies([g])
    assert len(live) == 1, "terminal-state rows are kept regardless of rig_path"
    assert hidden == 0


def test_partition_zombies_keeps_runs_with_no_rig_path() -> None:
    """Ad-hoc / scratch flows have no rig_path parameter — they aren't
    zombies, they're just parameter-less (and shown with rig=`-`)."""
    g = _status.IssueGroup(
        issue_id="ad-hoc",
        latest=FakeFlowRun(
            id="x",
            name="ad-hoc",
            tags=["issue_id:ad-hoc"],
            state_name="Running",
            parameters=None,
        ),
        extras=[],
        current_step="-",
    )
    live, hidden = _status.partition_zombies([g])
    assert len(live) == 1
    assert hidden == 0


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


@pytest.mark.asyncio
async def test_find_runs_uses_expected_start_time_filter() -> None:
    """Regression: `--since` must filter on expected_start_time, not start_time.

    A newly dispatched flow run (PENDING/SCHEDULED) has start_time=null.
    Filtering with FlowRunFilterStartTime(after_=since) uses a `start_time > ?`
    SQL predicate that excludes null values, making in-flight runs invisible.
    FlowRunFilterExpectedStartTime is always set at dispatch time, so it
    correctly includes new runs.
    """
    client = _FakeClient([])
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    await _status.find_runs_by_issue_id(client, since=since)
    flt = client.call_kwargs.get("flow_run_filter")
    assert flt is not None
    # Must use expected_start_time, not start_time.
    assert flt.expected_start_time is not None, (
        "expected_start_time filter not set — newly dispatched PENDING runs would be invisible"
    )
    assert flt.start_time is None, (
        "start_time filter must not be set (excludes null-start_time runs)"
    )


def test_group_by_issue_null_start_time_run_visible() -> None:
    """Regression: a RUNNING run with start_time=None but expected_start_time set
    must appear in group_by_issue results (was hidden when sorted only by start_time).
    """
    t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
    runs = [
        FakeFlowRun(
            id="running-1",
            name="software_dev_fast",
            tags=["issue_id:sb-595"],
            state_name="Running",
            expected_start_time=t0,
            start_time=None,  # null — as seen on newly dispatched flows
        ),
    ]
    groups = _status.group_by_issue(runs)
    assert len(groups) == 1
    assert groups[0].latest.id == "running-1"


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


# ─── watchdog helpers ─────────────────────────────────────────────────


def test_run_dir_max_mtime_returns_max(tmp_path) -> None:
    f1 = tmp_path / "a.txt"
    f1.write_text("x")
    f2 = tmp_path / "sub" / "b.txt"
    f2.parent.mkdir()
    f2.write_text("y")
    # Touch f1 to a fixed time in the past, f2 to now.
    import os

    os.utime(f1, (1000.0, 1000.0))
    result = _status._run_dir_max_mtime(tmp_path)
    assert result is not None
    assert result >= f2.stat().st_mtime


def test_run_dir_max_mtime_empty_dir(tmp_path) -> None:
    assert _status._run_dir_max_mtime(tmp_path) is None


def test_has_live_process_tmux_match(monkeypatch: pytest.MonkeyPatch) -> None:

    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stdout = "po-my_issue-builder: 1 windows\n"
            stderr = ""

        return R()

    monkeypatch.setattr(_status.shutil, "which", lambda x: "/usr/bin/" + x)
    monkeypatch.setattr(_status.subprocess, "run", fake_run)
    assert _status._has_live_process("my.issue") is True


def test_has_live_process_pgrep_match(monkeypatch: pytest.MonkeyPatch) -> None:
    call_log: list[list] = []

    def fake_run(cmd, **kw):
        call_log.append(cmd)

        class R:
            returncode = 0 if cmd[0] == "pgrep" else 1
            stdout = "12345\n" if cmd[0] == "pgrep" else ""
            stderr = ""

        return R()

    # tmux not found, pgrep found
    monkeypatch.setattr(
        _status.shutil, "which", lambda x: None if x == "tmux" else "/usr/bin/" + x
    )
    monkeypatch.setattr(_status.subprocess, "run", fake_run)
    assert _status._has_live_process("my-issue") is True


def test_has_live_process_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd, **kw):
        class R:
            returncode = 1
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr(_status.shutil, "which", lambda x: "/usr/bin/" + x)
    monkeypatch.setattr(_status.subprocess, "run", fake_run)
    assert _status._has_live_process("my-issue") is False


def test_compute_stale_secs_no_bd(monkeypatch: pytest.MonkeyPatch) -> None:
    from prefect_orchestration.run_lookup import RunDirNotFound

    monkeypatch.setattr(
        "prefect_orchestration.status._bd_show_json_for_stale",
        lambda _: (_ for _ in ()).throw(RunDirNotFound("no bd")),
        raising=False,
    )
    # Patch at the import site inside compute_stale_secs
    import prefect_orchestration.run_lookup as _rl

    monkeypatch.setattr(
        _rl,
        "_bd_show_json",
        lambda *a, **k: (_ for _ in ()).throw(RunDirNotFound("no bd")),
    )
    result = _status.compute_stale_secs("missing-issue")
    assert result is None


def test_compute_stale_secs_no_run_dir_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import prefect_orchestration.run_lookup as _rl

    monkeypatch.setattr(_rl, "_bd_show_json", lambda *a, **k: {"metadata": {}})
    assert _status.compute_stale_secs("some-issue") is None


def test_compute_stale_secs_returns_elapsed(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import time as _time

    f = tmp_path / "artifact.md"
    f.write_text("done")
    known_mtime = _time.time() - 120  # 2 min ago
    os.utime(f, (known_mtime, known_mtime))

    import prefect_orchestration.run_lookup as _rl

    monkeypatch.setattr(
        _rl,
        "_bd_show_json",
        lambda *a, **k: {"metadata": {"po.run_dir": str(tmp_path)}},
    )
    result = _status.compute_stale_secs("test-issue")
    assert result is not None
    assert 110 <= result <= 130  # ~120s ± tolerance


# ─── watchdog_fail_stale_runs ─────────────────────────────────────────


@dataclass
class _FakeFlowRunWatchdog:
    id: str
    state_name: str = "Running"
    tags: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


class _FakeWatchdogClient:
    def __init__(self):
        self.set_flow_run_state_calls: list[tuple] = []

    async def set_flow_run_state(self, run_id, state, *, force=False):
        self.set_flow_run_state_calls.append((run_id, state, force))


@pytest.mark.asyncio
async def test_watchdog_skips_non_running(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeWatchdogClient()
    g = _status.IssueGroup(
        issue_id="x",
        latest=_FakeFlowRunWatchdog(id="r1", state_name="Completed"),
        extras=[],
        stale_secs=700,
    )
    failed = await _status.watchdog_fail_stale_runs(client, [g], fail_after_secs=600)
    assert failed == []
    assert client.set_flow_run_state_calls == []


@pytest.mark.asyncio
async def test_watchdog_skips_when_live_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_status, "_has_live_process", lambda _: True)
    client = _FakeWatchdogClient()
    g = _status.IssueGroup(
        issue_id="po-live",
        latest=_FakeFlowRunWatchdog(id="r2", state_name="Running"),
        extras=[],
        stale_secs=700,
    )
    failed = await _status.watchdog_fail_stale_runs(client, [g], fail_after_secs=600)
    assert failed == []
    assert client.set_flow_run_state_calls == []


@pytest.mark.asyncio
async def test_watchdog_skips_when_not_stale_enough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_status, "_has_live_process", lambda _: False)
    client = _FakeWatchdogClient()
    g = _status.IssueGroup(
        issue_id="po-fresh",
        latest=_FakeFlowRunWatchdog(id="r3", state_name="Running"),
        extras=[],
        stale_secs=300,  # below 600 threshold
    )
    failed = await _status.watchdog_fail_stale_runs(client, [g], fail_after_secs=600)
    assert failed == []
    assert client.set_flow_run_state_calls == []


@pytest.mark.asyncio
async def test_watchdog_fails_stale_dead_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_status, "_has_live_process", lambda _: False)
    bd_calls: list[list] = []

    def fake_run(cmd, **kw):
        bd_calls.append(cmd)

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(_status.subprocess, "run", fake_run)
    client = _FakeWatchdogClient()
    g = _status.IssueGroup(
        issue_id="po-dead",
        latest=_FakeFlowRunWatchdog(id="run-uuid-1", state_name="Running"),
        extras=[],
        stale_secs=700,
    )
    failed = await _status.watchdog_fail_stale_runs(client, [g], fail_after_secs=600)
    assert failed == ["po-dead"]
    # Prefect state transition was called
    assert len(client.set_flow_run_state_calls) == 1
    run_id, state, force = client.set_flow_run_state_calls[0]
    assert run_id == "run-uuid-1"
    assert force is True
    assert "Worker silent kill" in state.message
    # bd assignee clear was called
    assert any(cmd[:4] == ["bd", "update", "po-dead", "--assignee"] for cmd in bd_calls)


# ─── render_table stale annotation ───────────────────────────────────


def test_render_table_shows_stale_annotation() -> None:
    t0 = datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc)
    g = _status.IssueGroup(
        issue_id="po-stale",
        latest=FakeFlowRun(
            id="x",
            name="software_dev_full",
            tags=["issue_id:po-stale"],
            state_name="Running",
            start_time=t0,
        ),
        extras=[],
        stale_secs=360,  # 6 minutes — above STALE_WARN_SECS=300
    )
    out = _status.render_table([g])
    assert "(stale: 6m)" in out


def test_render_table_no_annotation_below_warn_threshold() -> None:
    t0 = datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc)
    g = _status.IssueGroup(
        issue_id="po-fresh2",
        latest=FakeFlowRun(
            id="y",
            name="software_dev_full",
            tags=["issue_id:po-fresh2"],
            state_name="Running",
            start_time=t0,
        ),
        extras=[],
        stale_secs=240,  # below 300 threshold
    )
    out = _status.render_table([g])
    assert "stale" not in out
