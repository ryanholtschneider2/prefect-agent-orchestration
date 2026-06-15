"""Regression tests for CLI backend error reporting and parsing."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from prefect_orchestration.agent_session import ClaudeCliBackend, CodexCliBackend


def test_nonzero_exit_includes_stdout_and_argv(tmp_path: Path) -> None:
    """Non-zero exit RuntimeError must surface stdout (and argv) for diagnosis.

    Reproduces the 2026-04-24 incident where empty stderr made three
    concurrent builder crashes undiagnosable.
    """
    completed = subprocess.CompletedProcess(
        args=["claude"],
        returncode=2,
        stdout="boom-on-stdout-marker",
        stderr="",
    )
    with patch(
        "prefect_orchestration.agent_session.subprocess.run", return_value=completed
    ):
        with pytest.raises(RuntimeError) as excinfo:
            ClaudeCliBackend().run(
                "hello",
                session_id=None,
                cwd=tmp_path,
            )
    msg = str(excinfo.value)
    assert "boom-on-stdout-marker" in msg
    assert "stdout:" in msg
    assert "exited 2" in msg
    # argv should also appear so the failure is reproducible by copy-paste.
    assert "argv:" in msg
    assert "claude" in msg


def test_successful_run_unchanged(tmp_path: Path) -> None:
    """Happy path: rc==0 returns (result, session_id) without raising."""
    envelope = json.dumps({"type": "result", "result": "ok", "session_id": "sid-123"})
    completed = subprocess.CompletedProcess(
        args=["claude"],
        returncode=0,
        stdout=envelope,
        stderr="",
    )
    with patch(
        "prefect_orchestration.agent_session.subprocess.run", return_value=completed
    ):
        result, sid = ClaudeCliBackend().run(
            "hello",
            session_id=None,
            cwd=tmp_path,
        )
    assert result == "ok"
    assert sid == "sid-123"


def test_codex_nonzero_exit_includes_stdout_and_argv(tmp_path: Path) -> None:
    completed = subprocess.CompletedProcess(
        args=["codex"],
        returncode=3,
        stdout="codex-stdout-marker",
        stderr="codex-stderr-marker",
    )
    with patch(
        "prefect_orchestration.agent_session.subprocess.run", return_value=completed
    ):
        with pytest.raises(RuntimeError) as excinfo:
            CodexCliBackend().run(
                "hello",
                session_id=None,
                cwd=tmp_path,
            )
    msg = str(excinfo.value)
    assert "codex-stdout-marker" in msg
    assert "codex-stderr-marker" in msg
    assert "argv:" in msg
    assert "codex exec exited 3" in msg


def test_codex_successful_run_parses_jsonl(tmp_path: Path) -> None:
    stdout = "\n".join(
        [
            '{"type":"thread.started","thread_id":"tid-123"}',
            "transport warning",
            '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}',
            '{"type":"turn.completed","usage":{"output_tokens":1}}',
        ]
    )
    completed = subprocess.CompletedProcess(
        args=["codex"],
        returncode=0,
        stdout=stdout,
        stderr="",
    )
    with patch(
        "prefect_orchestration.agent_session.subprocess.run", return_value=completed
    ):
        result, sid = CodexCliBackend().run(
            "hello",
            session_id=None,
            cwd=tmp_path,
        )
    assert result == "ok"
    assert sid == "tid-123"


def test_codex_run_ignores_model_flag_for_cli_compatibility(tmp_path: Path) -> None:
    completed = subprocess.CompletedProcess(
        args=["codex"],
        returncode=0,
        stdout='{"type":"thread.started","thread_id":"tid-123"}\n',
        stderr="",
    )
    with patch(
        "prefect_orchestration.agent_session.subprocess.run", return_value=completed
    ) as run_mock:
        CodexCliBackend().run(
            "hello",
            session_id=None,
            cwd=tmp_path,
            model="gpt-5-codex",
        )
    cmd = run_mock.call_args.kwargs.get("args") or run_mock.call_args.args[0]
    assert "-m" not in cmd
    assert "gpt-5-codex" not in cmd


# A well-formed session id so `_build_claude_argv` actually emits `--resume`.
_RESUME_SID = "11111111-2222-3333-4444-555555555555"


def _envelope(result: str, sid: str) -> str:
    return json.dumps({"type": "result", "result": result, "session_id": sid})


def _completed(
    returncode: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _argv_of(call) -> list[str]:
    """Pull the argv that a mocked subprocess.run was invoked with."""
    return call.kwargs.get("args") or call.args[0]


def test_resume_transient_failure_retries_then_succeeds(tmp_path: Path) -> None:
    """A transient non-zero on resume retries once and returns the result."""
    first = _completed(1, stdout="", stderr="transient blip")
    second = _completed(0, stdout=_envelope("recovered", _RESUME_SID))
    with (
        patch(
            "prefect_orchestration.agent_session.subprocess.run",
            side_effect=[first, second],
        ) as run_mock,
        patch("prefect_orchestration.agent_session.time.sleep"),
    ):
        result, sid = ClaudeCliBackend().run(
            "hello", session_id=_RESUME_SID, cwd=tmp_path
        )
    assert result == "recovered"
    assert sid == _RESUME_SID
    # Both attempts were resume attempts (same --resume <sid> argv).
    assert run_mock.call_count == 2
    for call in run_mock.call_args_list:
        assert "--resume" in _argv_of(call)


def test_resume_session_gone_falls_back_to_fresh_session(tmp_path: Path) -> None:
    """Session-not-found skips the retry and starts a brand-new session."""
    gone = _completed(
        1, stdout="", stderr="No conversation found with session ID: " + _RESUME_SID
    )
    fresh = _completed(0, stdout=_envelope("fresh-work", "fresh-sid-999"))
    with patch(
        "prefect_orchestration.agent_session.subprocess.run",
        side_effect=[gone, fresh],
    ) as run_mock:
        result, sid = ClaudeCliBackend().run(
            "hello", session_id=_RESUME_SID, cwd=tmp_path
        )
    assert result == "fresh-work"
    assert sid == "fresh-sid-999"
    # Exactly two calls: the failed resume + the fresh fallback (no retry).
    assert run_mock.call_count == 2
    # First attempt resumed; the fallback must NOT carry --resume.
    assert "--resume" in _argv_of(run_mock.call_args_list[0])
    assert "--resume" not in _argv_of(run_mock.call_args_list[1])


def test_resume_fails_twice_falls_back_to_fresh_session(tmp_path: Path) -> None:
    """Transient resume failure that persists falls back to a fresh session."""
    first = _completed(1, stdout="", stderr="transient blip")
    retry = _completed(1, stdout="", stderr="transient blip again")
    fresh = _completed(0, stdout=_envelope("fresh-work", "fresh-sid-777"))
    with (
        patch(
            "prefect_orchestration.agent_session.subprocess.run",
            side_effect=[first, retry, fresh],
        ) as run_mock,
        patch("prefect_orchestration.agent_session.time.sleep"),
    ):
        result, sid = ClaudeCliBackend().run(
            "hello", session_id=_RESUME_SID, cwd=tmp_path
        )
    assert result == "fresh-work"
    assert sid == "fresh-sid-777"
    assert run_mock.call_count == 3
    assert "--resume" in _argv_of(run_mock.call_args_list[0])
    assert "--resume" in _argv_of(run_mock.call_args_list[1])
    assert "--resume" not in _argv_of(run_mock.call_args_list[2])


def test_first_turn_failure_still_raises(tmp_path: Path) -> None:
    """No session_id means this is a first turn — a failure is still fatal."""
    completed = _completed(2, stdout="boom-on-stdout-marker", stderr="")
    with patch(
        "prefect_orchestration.agent_session.subprocess.run", return_value=completed
    ) as run_mock:
        with pytest.raises(RuntimeError) as excinfo:
            ClaudeCliBackend().run("hello", session_id=None, cwd=tmp_path)
    assert "exited 2" in str(excinfo.value)
    # No fallback attempt — first-turn failures are not retried.
    assert run_mock.call_count == 1


def test_resume_success_path_unchanged(tmp_path: Path) -> None:
    """A resume that succeeds on the first try returns immediately, no retry."""
    completed = _completed(0, stdout=_envelope("ok", _RESUME_SID))
    with patch(
        "prefect_orchestration.agent_session.subprocess.run", return_value=completed
    ) as run_mock:
        result, sid = ClaudeCliBackend().run(
            "hello", session_id=_RESUME_SID, cwd=tmp_path
        )
    assert result == "ok"
    assert sid == _RESUME_SID
    assert run_mock.call_count == 1


def test_codex_resume_session_gone_falls_back_to_fresh_session(tmp_path: Path) -> None:
    """Codex backend gets the same resume resilience as the claude backend."""
    gone = subprocess.CompletedProcess(
        args=["codex"],
        returncode=1,
        stdout="",
        stderr="No conversation found",
    )
    fresh = subprocess.CompletedProcess(
        args=["codex"],
        returncode=0,
        stdout='{"type":"thread.started","thread_id":"tid-new"}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}',
        stderr="",
    )
    with patch(
        "prefect_orchestration.agent_session.subprocess.run",
        side_effect=[gone, fresh],
    ) as run_mock:
        result, sid = CodexCliBackend().run(
            "hello", session_id=_RESUME_SID, cwd=tmp_path
        )
    assert result == "ok"
    assert sid == "tid-new"
    assert run_mock.call_count == 2
    assert "resume" in _argv_of(run_mock.call_args_list[0])
    assert "resume" not in _argv_of(run_mock.call_args_list[1])
