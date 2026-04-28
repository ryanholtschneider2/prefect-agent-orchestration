"""Tests for the tmux_tracker module (sav.3).

In-process registry behaviour is pure-Python (no subprocess); cross-process
scanners (`kill_all`, `kill_for_issue`) are tested by patching
`subprocess.run` and `shutil.which`.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from prefect_orchestration import tmux_tracker
from prefect_orchestration.tmux_tracker import TmuxRef


@pytest.fixture(autouse=True)
def _clean_registry():
    tmux_tracker._LIVE.clear()
    yield
    tmux_tracker._LIVE.clear()


def test_register_and_snapshot():
    ref = TmuxRef(
        session_name="po-iss-builder", window_name=None, target="po-iss-builder"
    )
    tmux_tracker.register(ref)
    assert tmux_tracker.snapshot() == [ref]


def test_register_dedup_same_ref():
    ref = TmuxRef(session_name="s", window_name=None, target="s")
    tmux_tracker.register(ref)
    tmux_tracker.register(ref)
    assert len(tmux_tracker.snapshot()) == 1


def test_unregister_by_target_idempotent():
    ref = TmuxRef(session_name="s", window_name=None, target="s")
    tmux_tracker.register(ref)
    tmux_tracker.unregister_by_target("s")
    tmux_tracker.unregister_by_target("s")  # no error
    assert tmux_tracker.snapshot() == []


def test_kill_all_uses_correct_kill_kind(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(
        "prefect_orchestration.tmux_tracker.shutil.which", lambda _: "/usr/bin/tmux"
    )
    monkeypatch.setattr("prefect_orchestration.tmux_tracker.subprocess.run", fake_run)

    tmux_tracker.register(TmuxRef("po-rig-epic", "iss_3-builder", "@42"))
    tmux_tracker.register(TmuxRef("po-iss_3-builder", None, "po-iss_3-builder"))
    n = tmux_tracker.kill_all()

    assert n == 2
    kinds = sorted(c[1] for c in calls)
    assert kinds == ["kill-session", "kill-window"]
    # Targets should match what was registered.
    targets = sorted(c[3] for c in calls)
    assert targets == ["@42", "po-iss_3-builder"]
    # Registry should be drained.
    assert tmux_tracker.snapshot() == []


def test_kill_all_no_tmux_returns_zero(monkeypatch):
    monkeypatch.setattr(
        "prefect_orchestration.tmux_tracker.shutil.which", lambda _: None
    )
    tmux_tracker.register(TmuxRef("s", None, "s"))
    assert tmux_tracker.kill_all() == 0
    # Registry is still cleared (best-effort).
    assert tmux_tracker.snapshot() == []


def test_kill_for_issue_no_tmux(monkeypatch):
    monkeypatch.setattr(
        "prefect_orchestration.tmux_tracker.shutil.which", lambda _: None
    )
    assert tmux_tracker.kill_for_issue("anything") == 0


def test_kill_for_issue_unscoped_session(monkeypatch):
    """`po-{safe_issue}-{role}` session for the issue is killed whole."""
    monkeypatch.setattr(
        "prefect_orchestration.tmux_tracker.shutil.which", lambda _: "/usr/bin/tmux"
    )
    sessions = ["po-foo_3-builder", "po-foo_3-tester", "po-other-builder", "unrelated"]

    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs: Any) -> subprocess.CompletedProcess[Any]:
        calls.append(list(cmd))
        if cmd[:2] == ["tmux", "list-sessions"]:
            return subprocess.CompletedProcess(cmd, 0, "\n".join(sessions) + "\n", "")
        if cmd[:2] == ["tmux", "list-windows"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["tmux", "kill-session"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 1, "", "")

    monkeypatch.setattr("prefect_orchestration.tmux_tracker.subprocess.run", fake_run)

    n = tmux_tracker.kill_for_issue("foo.3")
    assert n == 2
    killed = sorted(c[3] for c in calls if c[1] == "kill-session")
    assert killed == ["po-foo_3-builder", "po-foo_3-tester"]


def test_kill_for_issue_scoped_window(monkeypatch):
    """In a shared `po-{rig}` session, only the issue's windows are killed."""
    monkeypatch.setattr(
        "prefect_orchestration.tmux_tracker.shutil.which", lambda _: "/usr/bin/tmux"
    )
    sessions = ["po-myrig", "po-myrig-epic42"]
    windows_by_session = {
        "po-myrig": ["foo_3-builder", "foo_3-tester", "bar-builder"],
        "po-myrig-epic42": ["foo_3-critic", "baz-builder"],
    }

    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs: Any) -> subprocess.CompletedProcess[Any]:
        calls.append(list(cmd))
        if cmd[:2] == ["tmux", "list-sessions"]:
            return subprocess.CompletedProcess(cmd, 0, "\n".join(sessions) + "\n", "")
        if cmd[:2] == ["tmux", "list-windows"]:
            sess = cmd[cmd.index("-t") + 1]
            return subprocess.CompletedProcess(
                cmd, 0, "\n".join(windows_by_session.get(sess, [])) + "\n", ""
            )
        if cmd[:2] == ["tmux", "kill-window"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 1, "", "")

    monkeypatch.setattr("prefect_orchestration.tmux_tracker.subprocess.run", fake_run)

    n = tmux_tracker.kill_for_issue("foo.3")
    # 2 from po-myrig (foo_3-builder, foo_3-tester) + 1 from po-myrig-epic42 (foo_3-critic) = 3
    assert n == 3
    killed_targets = sorted(c[3] for c in calls if c[1] == "kill-window")
    assert killed_targets == [
        "po-myrig-epic42:foo_3-critic",
        "po-myrig:foo_3-builder",
        "po-myrig:foo_3-tester",
    ]


def test_kill_for_issue_dot_sanitization(monkeypatch):
    """Issue ids with dots map to underscores in tmux names."""
    monkeypatch.setattr(
        "prefect_orchestration.tmux_tracker.shutil.which", lambda _: "/usr/bin/tmux"
    )
    sessions = ["po-prefect-orchestration-sav_3-builder"]

    def fake_run(cmd, **_kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if cmd[:2] == ["tmux", "list-sessions"]:
            return subprocess.CompletedProcess(cmd, 0, "\n".join(sessions) + "\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("prefect_orchestration.tmux_tracker.subprocess.run", fake_run)
    assert tmux_tracker.kill_for_issue("prefect-orchestration-sav.3") == 1
