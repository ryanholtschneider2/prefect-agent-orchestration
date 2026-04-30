"""`--effort` plumbing through `_build_claude_argv` + AgentSession."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from prefect_orchestration.agent_session import (
    AgentSession,
    ClaudeCliBackend,
    StubBackend,
    _build_claude_argv,
)


def test_build_claude_argv_appends_effort_when_set() -> None:
    argv = _build_claude_argv(
        "claude --dangerously-skip-permissions",
        session_id=None,
        fork=False,
        model="opus",
        effort="low",
    )
    assert "--effort" in argv
    idx = argv.index("--effort")
    assert argv[idx + 1] == "low"


def test_build_claude_argv_skips_effort_when_none() -> None:
    """Default behaviour: no `--effort` flag → claude CLI picks its own default."""
    argv = _build_claude_argv(
        "claude --dangerously-skip-permissions",
        session_id=None,
        fork=False,
        model="opus",
    )
    assert "--effort" not in argv


def test_build_claude_argv_skips_effort_when_empty_string() -> None:
    argv = _build_claude_argv(
        "claude", session_id=None, fork=False, model="opus", effort=""
    )
    assert "--effort" not in argv


def test_cli_backend_passes_effort_to_argv(tmp_path: Path) -> None:
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=json.dumps({"type": "result", "result": "ok", "session_id": "sid"}),
            stderr="",
        )

    with patch(
        "prefect_orchestration.agent_session.subprocess.run", side_effect=fake_run
    ):
        ClaudeCliBackend().run(
            "hi",
            session_id=None,
            cwd=tmp_path,
            model="haiku",
            effort="max",
        )
    cmd = captured["cmd"]
    assert "--effort" in cmd
    assert cmd[cmd.index("--effort") + 1] == "max"
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "haiku"


def test_agent_session_threads_effort_to_backend(tmp_path: Path) -> None:
    """AgentSession.prompt forwards `self.effort` to backend.run()."""

    captured: dict = {}

    class _Capture:
        def run(self, prompt, **kwargs):
            captured["kwargs"] = dict(kwargs)
            return ("ok", "sid-1")

    sess = AgentSession(
        role="builder",
        repo_path=tmp_path,
        backend=_Capture(),
        model="sonnet",
        effort="high",
        overlay=False,
        skills=False,
    )
    sess.prompt("go")
    assert captured["kwargs"]["effort"] == "high"
    assert captured["kwargs"]["model"] == "sonnet"


def test_agent_session_fork_propagates_effort(tmp_path: Path) -> None:
    sess = AgentSession(
        role="critic",
        repo_path=tmp_path,
        backend=StubBackend(),
        session_id="00000000-0000-4000-8000-000000000000",
        model="opus",
        effort="max",
    )
    child = sess.fork()
    assert child.effort == "max"
    assert child.model == "opus"


def test_stub_backend_accepts_effort_kwarg(tmp_path: Path) -> None:
    """Stub backend must accept (and ignore) the new effort kwarg."""
    out, sid = StubBackend().run(
        "hi", session_id=None, cwd=tmp_path, model="opus", effort="low"
    )
    assert "ack" in out
    assert sid.startswith("stub-") or sid
