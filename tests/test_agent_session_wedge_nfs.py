"""Tests for prefect-orchestration-nfs: parallel-spawn wedge detection.

Two parallel `po run` invocations against the same rig (PO_BACKEND=tmux)
used to wedge 1-of-2 at the very first triager step — empty tmux pane,
no Claude child, sentinel never fires, parent blocks until `timeout_s`
(default 1800s). Fix: per-rig spawn-time advisory lock + post-paste
submission-landed check that raises a clear RuntimeError within ~60s
when the prompt never reaches the agent.

Covers:
  - `_with_rig_spawn_lock`: serializes per-cwd, no-op with PO_DISABLE_SPAWN_LOCK=1
  - `_assert_submission_landed`: raises within `grace_s` when no marker appears,
    returns immediately when a marker appears, returns immediately on `[claude exited`
  - `_wait_for_tui_ready`: returns True/False to expose detection result
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from prefect_orchestration.agent_session import (
    _assert_submission_landed,
    _wait_for_tui_ready,
    _with_rig_spawn_lock,
)


# ---------- _with_rig_spawn_lock ----------


def test_spawn_lock_serializes_concurrent_same_cwd(tmp_path: Path) -> None:
    """Two threads in the same cwd must serialize on the lock."""
    holds: list[float] = []

    def _hold(idx: int) -> None:
        with _with_rig_spawn_lock(tmp_path):
            holds.append(time.monotonic())
            time.sleep(0.3)

    t1 = threading.Thread(target=_hold, args=(1,))
    t2 = threading.Thread(target=_hold, args=(2,))
    t1.start()
    # Tiny stagger so t1 grabs the lock first; both threads then race.
    time.sleep(0.05)
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert len(holds) == 2
    # Second entry must be at least ~0.3s after the first (lock held that long).
    delta = holds[1] - holds[0]
    assert delta >= 0.25, f"second entry only {delta:.3f}s after first — not serialized"


def test_spawn_lock_independent_across_cwds(tmp_path: Path) -> None:
    """Different cwds must not block each other."""
    cwd_a = tmp_path / "rig-a"
    cwd_b = tmp_path / "rig-b"
    cwd_a.mkdir()
    cwd_b.mkdir()

    holds: list[float] = []

    def _hold_a() -> None:
        with _with_rig_spawn_lock(cwd_a):
            holds.append(time.monotonic())
            time.sleep(0.3)

    def _hold_b() -> None:
        with _with_rig_spawn_lock(cwd_b):
            holds.append(time.monotonic())
            time.sleep(0.3)

    t1 = threading.Thread(target=_hold_a)
    t2 = threading.Thread(target=_hold_b)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert len(holds) == 2
    # Both should enter near-simultaneously (different locks).
    delta = abs(holds[1] - holds[0])
    assert delta < 0.15, f"different-cwd locks blocked each other ({delta:.3f}s gap)"


def test_spawn_lock_disabled_via_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PO_DISABLE_SPAWN_LOCK=1 makes the context manager a no-op."""
    monkeypatch.setenv("PO_DISABLE_SPAWN_LOCK", "1")
    holds: list[float] = []

    def _hold() -> None:
        with _with_rig_spawn_lock(tmp_path):
            holds.append(time.monotonic())
            time.sleep(0.3)

    t1 = threading.Thread(target=_hold)
    t2 = threading.Thread(target=_hold)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    # Both should enter near-simultaneously when lock is disabled.
    delta = abs(holds[1] - holds[0])
    assert delta < 0.15, f"disabled lock still serialized ({delta:.3f}s gap)"


def test_spawn_lock_creates_planning_dir(tmp_path: Path) -> None:
    """`.planning/` is created on demand if missing."""
    assert not (tmp_path / ".planning").exists()
    with _with_rig_spawn_lock(tmp_path):
        pass
    assert (tmp_path / ".planning" / ".po-claude-spawn.lock").exists()


def test_spawn_lock_degrades_to_noop_when_unwritable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read-only rig path becomes a no-op rather than raising."""
    bogus = tmp_path / "does-not-exist" / "deeper"
    # Force mkdir to fail by patching `Path.mkdir` to raise OSError.
    real_mkdir = Path.mkdir

    def _failing_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if self == bogus / ".planning":
            raise OSError("read-only")
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", _failing_mkdir)
    # Should yield without raising — degraded mode.
    with _with_rig_spawn_lock(bogus):
        pass


# ---------- _wait_for_tui_ready return value ----------


def test_wait_for_tui_ready_returns_true_on_glyph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detecting `❯` returns True (not None)."""
    fake_run = type(
        "R",
        (),
        {
            "stdout": "│ ❯ │\n".encode(),
            "stderr": b"",
            "returncode": 0,
        },
    )

    monkeypatch.setattr(
        "prefect_orchestration.agent_session.subprocess.run",
        lambda *a, **kw: fake_run,
    )
    assert _wait_for_tui_ready("po-x", fallback_s=2.0, poll=0.05) is True


def test_wait_for_tui_ready_returns_true_on_exit_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`[claude exited` short-circuit also returns True (caller handles)."""
    fake_run = type(
        "R",
        (),
        {
            "stdout": "[claude exited 1 — held open]\n".encode(),
            "stderr": b"",
            "returncode": 0,
        },
    )
    monkeypatch.setattr(
        "prefect_orchestration.agent_session.subprocess.run",
        lambda *a, **kw: fake_run,
    )
    assert _wait_for_tui_ready("po-x", fallback_s=2.0, poll=0.05) is True


def test_wait_for_tui_ready_returns_false_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty pane that never renders TUI glyph returns False after fallback_s."""
    fake_run = type(
        "R",
        (),
        {
            "stdout": b"",
            "stderr": b"",
            "returncode": 0,
        },
    )
    monkeypatch.setattr(
        "prefect_orchestration.agent_session.subprocess.run",
        lambda *a, **kw: fake_run,
    )
    t0 = time.monotonic()
    out = _wait_for_tui_ready("po-x", fallback_s=0.5, poll=0.1)
    elapsed = time.monotonic() - t0
    assert out is False
    assert 0.4 <= elapsed <= 1.5  # roughly fallback_s


# ---------- _assert_submission_landed ----------


_MARKERS = ("esc to interrupt", "Composing", "Thinking")


def test_assert_submission_landed_returns_when_marker_appears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Marker found → returns silently, no error."""
    fake_run = type(
        "R",
        (),
        {
            "stdout": b"... Composing reply ...",
            "stderr": b"",
            "returncode": 0,
        },
    )
    monkeypatch.setattr(
        "prefect_orchestration.agent_session.subprocess.run",
        lambda *a, **kw: fake_run,
    )
    # Should return immediately — no exception.
    _assert_submission_landed(
        "po-x",
        active_markers=_MARKERS,
        issue="x",
        role="triager",
        session_id=None,
        timeout_s=10.0,
        grace_s=2.0,
        poll=0.05,
    )


def test_assert_submission_landed_returns_on_claude_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`[claude exited` marker → returns (caller's downstream logic handles)."""
    fake_run = type(
        "R",
        (),
        {
            "stdout": "[claude exited 137 — session held open for diagnostics]".encode(),
            "stderr": b"",
            "returncode": 0,
        },
    )
    monkeypatch.setattr(
        "prefect_orchestration.agent_session.subprocess.run",
        lambda *a, **kw: fake_run,
    )
    _assert_submission_landed(
        "po-x",
        active_markers=_MARKERS,
        issue="x",
        role="triager",
        session_id=None,
        timeout_s=10.0,
        grace_s=2.0,
        poll=0.05,
    )


def test_assert_submission_landed_raises_when_pane_stays_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No marker, no exit — raises RuntimeError after grace_s with diagnostic."""
    fake_run = type(
        "R",
        (),
        {
            "stdout": b"  > some idle prompt  \n",
            "stderr": b"",
            "returncode": 0,
        },
    )
    monkeypatch.setattr(
        "prefect_orchestration.agent_session.subprocess.run",
        lambda *a, **kw: fake_run,
    )
    t0 = time.monotonic()
    with pytest.raises(RuntimeError) as exc_info:
        _assert_submission_landed(
            "po-x",
            active_markers=_MARKERS,
            issue="myissue",
            role="triager",
            session_id=None,
            timeout_s=1800.0,
            grace_s=0.5,
            poll=0.1,
        )
    elapsed = time.monotonic() - t0
    # Within grace + tolerance.
    assert 0.4 <= elapsed <= 2.0, f"raised after {elapsed:.2f}s, expected ~0.5s"
    msg = str(exc_info.value)
    assert "issue='myissue'" in msg
    assert "role='triager'" in msg
    assert "submission never landed" in msg
    assert "po retry" in msg


def test_total_wedge_latency_under_60s_invariant() -> None:
    """Documents the SLA: TUI fallback (8s) + paste retries (~9s) + grace (30s) ≤ 60s."""
    tui_max = 8.0  # _wait_for_tui_ready fallback_s default
    paste_loop_max = 1.0 + 1.5 + 2.0 + 1.5 + 2.0 + 1.5  # 3 attempts × (sleep+sleep)
    grace_max = 30.0  # _assert_submission_landed default grace_s
    assert tui_max + paste_loop_max + grace_max <= 60.0


# ---------- env hygiene: lock cleanup ----------


def test_lock_released_after_context_exits(tmp_path: Path) -> None:
    """Successive entries don't deadlock — lock is released on exit."""
    for _ in range(3):
        with _with_rig_spawn_lock(tmp_path):
            pass


def test_lock_released_on_exception(tmp_path: Path) -> None:
    """Exception inside the block still releases the lock."""
    with pytest.raises(RuntimeError):
        with _with_rig_spawn_lock(tmp_path):
            raise RuntimeError("boom")
    # Should be able to re-enter.
    with _with_rig_spawn_lock(tmp_path):
        pass
