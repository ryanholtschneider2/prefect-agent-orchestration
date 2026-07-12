from __future__ import annotations

import subprocess

from prefect_orchestration import cancel


def test_kill_issue_tmux_is_scoped(monkeypatch) -> None:
    calls: list[list[str]] = []

    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["tmux", "list-sessions"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="po-issue_1-builder\npo-other-reviewer\n",
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(cancel.subprocess, "run", run)

    assert cancel._kill_issue_tmux("issue.1") == 1
    assert ["tmux", "kill-session", "-t", "po-issue_1-builder"] in calls
    assert ["tmux", "kill-session", "-t", "po-other-reviewer"] not in calls
