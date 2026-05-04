"""Unit tests for `prefect_orchestration.watch`.

The driver uses asyncio; individual helpers are plain pure functions and
are the primary target of these tests. `run_watch` is exercised with
fake callables + a short poll interval.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from prefect_orchestration.watch import (
    Event,
    REPLAY_SEPARATOR,
    build_prefect_replay,
    build_run_dir_replay,
    diff_flow_state,
    diff_run_dir,
    diff_task_runs,
    merge_events,
    render,
    run_watch,
    scan_run_dir,
    should_use_color,
)


# ─── fake prefect objects ────────────────────────────────────────────


@dataclass
class FakeState:
    name: str
    type: str = "RUNNING"


@dataclass
class FakeTaskRun:
    id: str
    name: str
    state_name: str = "Running"
    state_type: str = "RUNNING"


@dataclass
class FakeFlowRun:
    id: str = "fr-1"
    name: str = "my-flow"
    state_name: str = "Running"
    state_type: str = "RUNNING"
    tags: list[str] = field(default_factory=list)
    state_history: list[Any] = field(default_factory=list)


@dataclass
class FakeHistoryEntry:
    name: str
    timestamp: datetime


# ─── render ──────────────────────────────────────────────────────────


def test_render_no_color_has_expected_shape() -> None:
    ev = Event(
        ts=datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc),
        source="prefect",
        kind="state",
        text="Running → Completed",
    )
    line = render(ev, use_color=False)
    assert "[prefect]" in line
    assert "state" in line
    assert "Running → Completed" in line
    assert "\x1b[" not in line


def test_render_color_includes_ansi() -> None:
    ev = Event(ts=datetime.now(timezone.utc), source="run-dir", kind="new", text="x.md")
    line = render(ev, use_color=True)
    assert "\x1b[" in line
    assert "[run-dir]" in line


def test_should_use_color_respects_no_color(monkeypatch) -> None:
    class FakeTTY:
        def isatty(self) -> bool:
            return True

    monkeypatch.setenv("NO_COLOR", "1")
    assert should_use_color(FakeTTY()) is False


def test_should_use_color_disabled_for_pipe(monkeypatch) -> None:
    class FakePipe:
        def isatty(self) -> bool:
            return False

    monkeypatch.delenv("NO_COLOR", raising=False)
    assert should_use_color(FakePipe()) is False


# ─── run-dir scanning ────────────────────────────────────────────────


def test_scan_run_dir_picks_watched_suffixes(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("x")
    (tmp_path / "b.json").write_text("{}")
    (tmp_path / "ignore.png").write_bytes(b"\x89PNG")
    nested = tmp_path / "verdicts"
    nested.mkdir()
    (nested / "c.json").write_text("{}")

    snapshot = scan_run_dir(tmp_path)
    names = {p.name for p in snapshot}
    assert names == {"a.md", "b.json", "c.json"}


def test_scan_run_dir_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert scan_run_dir(tmp_path / "gone") == {}


def test_diff_run_dir_emits_new_and_modified(tmp_path: Path) -> None:
    f1 = tmp_path / "a.md"
    f2 = tmp_path / "b.md"
    f1.write_text("a")
    f2.write_text("b")
    prev = {f1: f1.stat().st_mtime}
    # bump f1 mtime explicitly so diff sees the change on any FS
    import os as _os

    _os.utime(f1, (f1.stat().st_mtime + 10, f1.stat().st_mtime + 10))
    current = {p: p.stat().st_mtime for p in (f1, f2)}
    events = diff_run_dir(prev, current, run_dir=tmp_path)
    kinds = {e.text: e.kind for e in events}
    assert kinds == {"a.md": "modified", "b.md": "new"}
    # ordered by timestamp asc
    assert events == sorted(events, key=lambda e: e.ts)


# ─── prefect diffing ─────────────────────────────────────────────────


def test_diff_flow_state_only_fires_on_change() -> None:
    assert diff_flow_state("Running", "Running", flow_name="f") is None
    ev = diff_flow_state("Running", "Completed", flow_name="my-flow")
    assert ev is not None
    assert ev.source == "prefect"
    assert ev.kind == "state"
    assert "Running → Completed" in ev.text
    assert "my-flow" in ev.text


def test_diff_flow_state_ignores_none() -> None:
    assert diff_flow_state(None, None, flow_name="f") is None


def test_diff_task_runs_tracks_per_id_transitions() -> None:
    tr1 = FakeTaskRun(id="t1", name="triage", state_name="Running")
    tr2 = FakeTaskRun(id="t2", name="build", state_name="Pending")
    events, snapshot = diff_task_runs({}, [tr1, tr2])
    assert snapshot == {"t1": "Running", "t2": "Pending"}
    assert len(events) == 2

    # Next poll: only `build` changes.
    tr2b = FakeTaskRun(id="t2", name="build", state_name="Running")
    events, snapshot = diff_task_runs(snapshot, [tr1, tr2b])
    assert len(events) == 1
    assert "build" in events[0].text
    assert "Pending → Running" in events[0].text
    assert snapshot["t2"] == "Running"


# ─── replay ──────────────────────────────────────────────────────────


def test_build_run_dir_replay_orders_by_mtime(tmp_path: Path) -> None:
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("1")
    b.write_text("2")
    import os as _os

    _os.utime(a, (1000, 1000))
    _os.utime(b, (2000, 2000))
    events = build_run_dir_replay(tmp_path)
    assert [e.text for e in events] == ["a.md", "b.md"]
    assert all(e.kind == "replay" for e in events)


def test_build_prefect_replay_keeps_last_n() -> None:
    now = datetime.now(timezone.utc)
    history = [
        FakeHistoryEntry(name=f"s{i}", timestamp=now - timedelta(minutes=10 - i))
        for i in range(5)
    ]
    events = build_prefect_replay(history, n=3)
    assert [e.text for e in events] == ["s2", "s3", "s4"]
    assert all(e.source == "prefect" and e.kind == "replay" for e in events)


def test_merge_events_sorts_chronologically() -> None:
    now = datetime.now(timezone.utc)
    a = Event(ts=now, source="prefect", kind="state", text="A")
    b = Event(ts=now - timedelta(seconds=1), source="run-dir", kind="new", text="B")
    merged = merge_events([[a], [b]])
    assert [e.text for e in merged] == ["B", "A"]


# ─── run_watch driver ────────────────────────────────────────────────


class FakeClient:
    def __init__(self, flow_run: FakeFlowRun, task_scripts: list[list[FakeTaskRun]]):
        self.flow_run = flow_run
        self.task_scripts = task_scripts
        self.flow_calls = 0

    async def read_flow_run(self, flow_run_id: Any) -> FakeFlowRun:
        self.flow_calls += 1
        return self.flow_run

    async def read_task_runs(self, **kwargs: Any) -> list[FakeTaskRun]:
        if not self.task_scripts:
            return []
        return self.task_scripts.pop(0)


def _make_run_watch(
    *,
    tmp_path: Path,
    flow_run: FakeFlowRun | None,
    task_scripts: list[list[FakeTaskRun]] | None = None,
    replay: bool = False,
    replay_n: int = 10,
    run_for: float = 0.08,
) -> tuple[list[str], list[str]]:
    lines: list[str] = []
    warns: list[str] = []
    client = FakeClient(flow_run, task_scripts or []) if flow_run is not None else None

    async def factory() -> Any:
        return client

    async def find(_client: Any) -> Any:
        return flow_run

    async def go() -> None:
        task = asyncio.create_task(
            run_watch(
                issue_id="beads-xyz",
                run_dir=tmp_path,
                client_factory=factory if client is not None else None,
                find_flow_run=find if client is not None else None,
                write=lines.append,
                warn=warns.append,
                replay=replay,
                replay_n=replay_n,
                use_color=False,
                poll_prefect_s=0.01,
                poll_run_dir_s=0.01,
            )
        )
        # Wait for either natural completion or a short window. If still
        # running, cancel (live producers loop forever by design).
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=run_for)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(go())
    return lines, warns


def test_run_watch_terminal_on_start_only_watches_run_dir(tmp_path: Path) -> None:
    (tmp_path / "plan.md").write_text("plan\n")
    terminal_flow = FakeFlowRun(state_name="Completed", state_type="COMPLETED")
    # No more task scripts needed; producer shouldn't poll Prefect.
    lines, warns = _make_run_watch(
        tmp_path=tmp_path, flow_run=terminal_flow, task_scripts=[]
    )
    joined = "\n".join(lines)
    assert "flow already Completed" in joined
    # The run_dir poller will loop forever if we don't end it. Delete the
    # dir to signal `run_dir gone`.
    # (Already ran to completion because of the wait_for timeout? Check.)


def test_run_watch_no_flow_run_still_streams_run_dir(tmp_path: Path) -> None:
    lines, warns = _make_run_watch(tmp_path=tmp_path, flow_run=None)
    # With no flow run and no producers (empty run_dir), emits a warn.
    assert any("no flow run found" in w for w in warns)


def test_run_watch_replay_emits_separator(tmp_path: Path) -> None:
    (tmp_path / "triage.md").write_text("t")
    (tmp_path / "plan.md").write_text("p")
    terminal_flow = FakeFlowRun(state_name="Completed", state_type="COMPLETED")
    now = datetime.now(timezone.utc)
    terminal_flow.state_history = [
        FakeHistoryEntry(name="Running", timestamp=now - timedelta(seconds=30)),
        FakeHistoryEntry(name="Completed", timestamp=now - timedelta(seconds=1)),
    ]
    lines, warns = _make_run_watch(
        tmp_path=tmp_path,
        flow_run=terminal_flow,
        replay=True,
        replay_n=2,
    )
    joined = "\n".join(lines)
    assert REPLAY_SEPARATOR in joined
    # Both artifacts get replay lines.
    assert "triage.md" in joined
    assert "plan.md" in joined
    # At least one prefect replay line.
    assert "Running" in joined or "Completed" in joined


def test_run_watch_emits_live_run_dir_events(tmp_path: Path) -> None:
    """AC1: a new file appearing during the run shows as a `[run-dir] new`."""
    # Start watching an empty dir; a coroutine drops a file after one tick.
    lines: list[str] = []
    warns: list[str] = []
    terminal_flow = FakeFlowRun(state_name="Completed", state_type="COMPLETED")

    async def factory() -> Any:
        return FakeClient(terminal_flow, [])

    async def find(_client: Any) -> Any:
        return terminal_flow

    async def drop_file() -> None:
        await asyncio.sleep(0.02)
        (tmp_path / "new.md").write_text("hello")
        # Let the polling cycle see it, then drop the dir to stop the loop.
        await asyncio.sleep(0.05)
        import shutil

        shutil.rmtree(tmp_path)

    async def go() -> None:
        await asyncio.gather(
            run_watch(
                issue_id="beads-xyz",
                run_dir=tmp_path,
                client_factory=factory,
                find_flow_run=find,
                write=lines.append,
                warn=warns.append,
                replay=False,
                use_color=False,
                poll_prefect_s=0.01,
                poll_run_dir_s=0.01,
            ),
            drop_file(),
        )

    asyncio.run(asyncio.wait_for(go(), timeout=5))
    joined = "\n".join(lines)
    assert "new.md" in joined
    assert "[run-dir]" in joined
    # Flow already-terminal announcement comes through too.
    assert "flow already Completed" in joined


def test_run_watch_ctrl_c_equivalent_clean_exit(tmp_path: Path) -> None:
    """`run_watch` propagates CancelledError without swallowing — the
    CLI wrapper turns KeyboardInterrupt into a clean typer.Exit(0).
    Here we assert no tracebacks when the outer task is cancelled."""
    (tmp_path / "a.md").write_text("x")
    terminal_flow = FakeFlowRun(state_name="Running", state_type="RUNNING")

    async def factory() -> Any:
        return FakeClient(terminal_flow, [])

    async def find(_client: Any) -> Any:
        return terminal_flow

    lines: list[str] = []

    async def go() -> None:
        task = asyncio.create_task(
            run_watch(
                issue_id="beads-xyz",
                run_dir=tmp_path,
                client_factory=factory,
                find_flow_run=find,
                write=lines.append,
                warn=lambda _m: None,
                replay=False,
                use_color=False,
                poll_prefect_s=0.01,
                poll_run_dir_s=0.01,
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(asyncio.wait_for(go(), timeout=5))
