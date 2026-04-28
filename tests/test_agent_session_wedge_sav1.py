"""Regression tests for prefect-orchestration-sav.1.

`_wait_for_stop` had no default deadline — when a Claude session got
rate-limited or otherwise wedged, the orchestrator polled the missing
sentinel forever (observed: storybook flow hung 12+ hours after a
rate limit at 02:47Z).

These tests pin the contract:

1. `_wait_for_stop` honours a finite timeout and raises `TimeoutError`
   roughly within the deadline when no sentinel ever appears.
2. `_format_wedge_error` produces a diagnostic string that names the
   issue, role, session_id, and includes a pane-tail snippet — so the
   operator can see the rate-limit dialog instead of guessing.
3. `TmuxInteractiveClaudeBackend` and `TmuxClaudeBackend` default
   `timeout_s` to a finite value (DEFAULT_AGENT_TIMEOUT_S) so brand-new
   instances can never wedge indefinitely.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from prefect_orchestration import agent_session as agsess
from prefect_orchestration.agent_session import (
    DEFAULT_AGENT_TIMEOUT_S,
    TmuxClaudeBackend,
    TmuxInteractiveClaudeBackend,
    _format_wedge_error,
    _wait_for_stop,
)


class _AliveTmux:
    """Stub for `subprocess.run(["tmux", "has-session", ...])` — always alive."""

    returncode = 0
    stdout = b""
    stderr = b""


def test_wait_for_stop_raises_timeout_when_sentinel_never_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If Claude never writes its sentinel, _wait_for_stop must bail."""
    sentinel = tmp_path / "deadbeef-dead-beef-dead-beefdeadbeef.stopped"

    # Pretend the tmux session stays alive the whole time so the loop
    # exercises the deadline path, not the "session disappeared" path.
    monkeypatch.setattr(
        agsess.subprocess,
        "run",
        lambda *a, **kw: _AliveTmux(),
    )

    start = time.monotonic()
    with pytest.raises(TimeoutError, match="did not fire within"):
        _wait_for_stop(sentinel, "po-test-builder", timeout=0.5, poll=0.05)
    elapsed = time.monotonic() - start
    # Generous upper bound — slow CI may add overhead, but we should
    # never sit there for the original 0-timeout-means-forever bug.
    assert elapsed < 5.0, f"timeout enforcement too slow: {elapsed:.2f}s"


def test_format_wedge_error_includes_issue_role_session_and_pane_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator-facing diagnostic must surface the rate-limit hint."""
    fake_pane = b"\n".join(
        [b"some earlier output"] * 5
        + [b"You've hit your limit. Resets at 14:00Z."]
        + [b"$ "] * 3
    )

    class _PaneCapture:
        returncode = 0
        stdout = fake_pane
        stderr = b""

    monkeypatch.setattr(
        agsess.subprocess,
        "run",
        lambda *a, **kw: _PaneCapture(),
    )

    msg = _format_wedge_error(
        target="po-prefect-orchestration-sav_1-builder",
        issue="prefect-orchestration-sav.1",
        role="builder",
        session_id="abcd1234-aaaa-bbbb-cccc-deadbeef0000",
        timeout_s=1800.0,
    )

    assert "prefect-orchestration-sav.1" in msg
    assert "builder" in msg
    assert "abcd1234-aaaa-bbbb-cccc-deadbeef0000" in msg
    assert "1800.0s" in msg
    assert "po-stops" in msg  # hint
    assert "hit your limit" in msg  # pane-tail snippet visible
    assert "--- pane tail ---" in msg


def test_format_wedge_error_handles_unknown_session_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resume-mode path triggers wedge detection before sid is known."""

    class _Empty:
        returncode = 0
        stdout = b""
        stderr = b""

    monkeypatch.setattr(agsess.subprocess, "run", lambda *a, **kw: _Empty())

    msg = _format_wedge_error(
        target="po-some-target",
        issue="my-issue",
        role="planner",
        session_id=None,
        timeout_s=300.0,
    )
    assert "unknown" in msg.lower()
    assert "my-issue" in msg
    assert "planner" in msg


def test_format_wedge_error_tolerates_tmux_capture_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If `tmux capture-pane` raises, we still build a useful error."""

    def _boom(*_a, **_kw):
        raise OSError("tmux missing")

    monkeypatch.setattr(agsess.subprocess, "run", _boom)

    msg = _format_wedge_error(
        target="po-x",
        issue="my-issue",
        role="builder",
        session_id="abcd1234-aaaa-bbbb-cccc-deadbeef0000",
        timeout_s=60.0,
    )
    assert "pane unavailable" in msg
    assert "my-issue" in msg


def test_backends_default_timeout_is_finite() -> None:
    """sav.1: new instances must not have timeout_s=None by default."""
    assert DEFAULT_AGENT_TIMEOUT_S is not None
    assert DEFAULT_AGENT_TIMEOUT_S > 0
    assert (
        TmuxInteractiveClaudeBackend(issue="x", role="builder").timeout_s
        == DEFAULT_AGENT_TIMEOUT_S
    )
    assert (
        TmuxClaudeBackend(issue="x", role="builder").timeout_s
        == DEFAULT_AGENT_TIMEOUT_S
    )
