"""Unit tests for `beads_meta.watch` (prefect-orchestration-7vs.1).

Mocks `bd show` via `subprocess.run` so the watch loop is exercised
without a real dolt-server. The shellout shape matches what the bd
client emits against either embedded-dolt or dolt-server, so coverage
is identical for both backends.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from typing import Any

import pytest

from prefect_orchestration import beads_meta
from prefect_orchestration.beads_meta import BeadEvent, watch


class _FakeBd:
    """Returns a queued sequence of `bd show` rows per bead.

    Each call to `bd show <id>` pops the next row from `rows[id]`; once
    exhausted the last row is repeated (steady-state). Missing ids
    return non-zero exit so `_bd_show` -> None.
    """

    def __init__(self, rows: dict[str, list[dict]]) -> None:
        self.rows = {bid: list(seq) for bid, seq in rows.items()}
        self.idx: dict[str, int] = {bid: 0 for bid in rows}
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **kw: Any) -> subprocess.CompletedProcess:
        self.calls.append(list(cmd))
        if cmd[:2] != ["bd", "show"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")
        bid = cmd[2]
        if bid not in self.rows:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not found")
        seq = self.rows[bid]
        i = min(self.idx[bid], len(seq) - 1)
        self.idx[bid] += 1
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps([seq[i]]), stderr=""
        )


@pytest.fixture
def fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make watch() spin its inner sleep almost instantly."""
    real_sleep = time.sleep
    monkeypatch.setattr(beads_meta.time, "sleep", lambda s: real_sleep(0.001))
    monkeypatch.setattr(beads_meta, "_bd_available", lambda: True)


def _row(bid: str, status: str = "open", updated_at: str = "2026-04-28T00:00:00Z") -> dict:
    return {"id": bid, "status": status, "updated_at": updated_at, "title": ""}


def test_watch_returns_close_event(monkeypatch: pytest.MonkeyPatch, fast_poll: None) -> None:
    fake = _FakeBd(
        {
            "a": [
                _row("a", "open"),
                _row("a", "in_progress"),
                _row("a", "closed", "2026-04-28T00:00:01Z"),
            ],
        }
    )
    monkeypatch.setattr(beads_meta.subprocess, "run", fake)

    events = watch({"a"}, event="close", poll_interval=0.01)

    assert len(events) == 1
    e = events[0]
    assert isinstance(e, BeadEvent)
    assert e.bead_id == "a"
    assert e.kind == "close"
    assert e.new_status == "closed"


def test_watch_status_flip_only(monkeypatch: pytest.MonkeyPatch, fast_poll: None) -> None:
    """event='status' fires on open->in_progress (no close needed)."""
    fake = _FakeBd(
        {
            "b": [
                _row("b", "open"),
                _row("b", "in_progress", "2026-04-28T00:00:01Z"),
            ],
        }
    )
    monkeypatch.setattr(beads_meta.subprocess, "run", fake)

    events = watch(["b"], event="status", poll_interval=0.01)

    assert len(events) == 1
    assert events[0].kind == "status"
    assert events[0].old_status == "open"
    assert events[0].new_status == "in_progress"


def test_watch_any_fires_on_mutate(monkeypatch: pytest.MonkeyPatch, fast_poll: None) -> None:
    """event='any' fires on updated_at advance with no status change."""
    fake = _FakeBd(
        {
            "c": [
                _row("c", "in_progress", "2026-04-28T00:00:00Z"),
                _row("c", "in_progress", "2026-04-28T00:00:05Z"),
            ],
        }
    )
    monkeypatch.setattr(beads_meta.subprocess, "run", fake)

    events = watch(["c"], event="any", poll_interval=0.01)

    assert len(events) == 1
    assert events[0].kind == "mutate"


def test_watch_close_ignores_pure_status_flip(
    monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    """event='close' must NOT return on open->in_progress; it waits for closed."""
    fake = _FakeBd(
        {
            "d": [
                _row("d", "open"),
                _row("d", "in_progress", "2026-04-28T00:00:01Z"),
                _row("d", "in_progress", "2026-04-28T00:00:02Z"),
                _row("d", "closed", "2026-04-28T00:00:03Z"),
            ],
        }
    )
    monkeypatch.setattr(beads_meta.subprocess, "run", fake)

    events = watch({"d"}, event="close", poll_interval=0.01)

    assert len(events) == 1
    assert events[0].kind == "close"


def test_watch_multiple_beads_racing(
    monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    """Two beads close on the same poll cycle -> two events returned."""
    fake = _FakeBd(
        {
            "x": [_row("x", "open"), _row("x", "closed", "2026-04-28T00:00:01Z")],
            "y": [_row("y", "open"), _row("y", "closed", "2026-04-28T00:00:01Z")],
            "z": [_row("z", "open"), _row("z", "open", "2026-04-28T00:00:00Z")],
        }
    )
    monkeypatch.setattr(beads_meta.subprocess, "run", fake)

    events = watch(["x", "y", "z"], event="close", poll_interval=0.01)

    ids = [e.bead_id for e in events]
    assert ids == ["x", "y"]  # input order preserved; z didn't transition
    assert all(e.kind == "close" for e in events)


def test_watch_timeout_returns_empty(
    monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    fake = _FakeBd({"q": [_row("q", "open")]})  # never transitions
    monkeypatch.setattr(beads_meta.subprocess, "run", fake)

    t0 = time.monotonic()
    events = watch(["q"], event="close", timeout=0.05, poll_interval=0.01)
    elapsed = time.monotonic() - t0

    assert events == []
    assert elapsed < 1.0  # well under timeout * safety


def test_watch_cancel_returns_empty(
    monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    fake = _FakeBd({"r": [_row("r", "open")]})
    monkeypatch.setattr(beads_meta.subprocess, "run", fake)

    cancel = threading.Event()
    cancel.set()  # pre-cancelled — first check returns []

    events = watch(["r"], event="close", poll_interval=0.01, cancel=cancel)

    assert events == []


def test_watch_unknown_bead_raises(
    monkeypatch: pytest.MonkeyPatch, fast_poll: None
) -> None:
    fake = _FakeBd({})  # any id will 404
    monkeypatch.setattr(beads_meta.subprocess, "run", fake)

    with pytest.raises(ValueError, match="unknown bead id"):
        watch(["does-not-exist"], event="close", poll_interval=0.01, timeout=0.01)


def test_watch_filestore_path_raises_not_implemented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC (c): FileStore fallback path raises NotImplementedError loudly."""
    monkeypatch.setattr(beads_meta, "_bd_available", lambda: False)

    with pytest.raises(NotImplementedError, match="bd"):
        watch(["whatever"], event="close")


def test_watch_validates_inputs(monkeypatch: pytest.MonkeyPatch, fast_poll: None) -> None:
    monkeypatch.setattr(beads_meta.subprocess, "run", _FakeBd({}))

    with pytest.raises(ValueError, match="non-empty"):
        watch([], event="close")
    with pytest.raises(ValueError, match="unknown watch event"):
        watch(["a"], event="bogus")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="poll_interval"):
        watch(["a"], event="close", poll_interval=0)
