"""Regression tests for CLI backend error reporting and parsing."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from prefect_orchestration.agent_session import (
    ClaudeCliBackend,
    CodexCliBackend,
    _is_resume_session_missing,
)


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


# ---------------------------------------------------------------------------
# _is_resume_session_missing helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stderr,stdout,expected",
    [
        ("No conversation found with session ID abc", "", True),
        ("", "No conversation found", True),
        ("session not found for id xyz", "", True),
        ("some unrelated error", "another unrelated message", False),
        ("rate limit exceeded", "", False),
        ("", "", False),
    ],
)
def test_is_resume_session_missing(stderr: str, stdout: str, expected: bool) -> None:
    assert _is_resume_session_missing(stderr, stdout) is expected


# ---------------------------------------------------------------------------
# ClaudeCliBackend resume-fallback tests
# ---------------------------------------------------------------------------

_VALID_SID = "12345678-1234-1234-1234-123456789abc"
_FRESH_ENVELOPE = json.dumps(
    {"type": "result", "result": "fresh-ok", "session_id": "new-sid-fresh"}
)


def _fail(
    rc: int = 1, stderr: str = "", stdout: str = ""
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["claude"], returncode=rc, stdout=stdout, stderr=stderr
    )


def _ok(result: str = "ok", sid: str = "new-sid") -> subprocess.CompletedProcess:
    envelope = json.dumps({"type": "result", "result": result, "session_id": sid})
    return subprocess.CompletedProcess(
        args=["claude"], returncode=0, stdout=envelope, stderr=""
    )


def test_resume_session_not_found_falls_back_to_fresh(tmp_path: Path) -> None:
    """'No conversation found' on --resume -> immediate fresh-session fallback, no retry."""
    side_effects = [
        _fail(rc=1, stderr="No conversation found with session ID"),
        _ok(result="fresh-ok", sid="fresh-sid"),
    ]
    with patch(
        "prefect_orchestration.agent_session.subprocess.run", side_effect=side_effects
    ) as mock_run:
        result, sid = ClaudeCliBackend().run(
            "hello",
            session_id=_VALID_SID,
            cwd=tmp_path,
        )
    assert result == "fresh-ok"
    assert sid == "fresh-sid"
    # Should have called subprocess.run exactly twice (no retry of the failed resume).
    assert mock_run.call_count == 2
    # First call includes --resume; second call (fresh) does NOT.
    first_cmd = (
        mock_run.call_args_list[0].kwargs.get("args")
        or mock_run.call_args_list[0].args[0]
    )
    second_cmd = (
        mock_run.call_args_list[1].kwargs.get("args")
        or mock_run.call_args_list[1].args[0]
    )
    assert "--resume" in first_cmd
    assert "--resume" not in second_cmd


def test_resume_transient_failure_retries_then_succeeds(tmp_path: Path) -> None:
    """Transient non-zero on --resume -> retry once, succeeds on retry."""
    side_effects = [
        _fail(rc=1, stderr="connection reset"),  # first resume attempt fails
        _ok(result="retry-ok", sid="retry-sid"),  # retry of same resume succeeds
    ]
    with patch(
        "prefect_orchestration.agent_session.subprocess.run", side_effect=side_effects
    ) as mock_run:
        with patch("prefect_orchestration.agent_session.time.sleep"):  # skip backoff
            result, sid = ClaudeCliBackend().run(
                "hello",
                session_id=_VALID_SID,
                cwd=tmp_path,
            )
    assert result == "retry-ok"
    assert sid == "retry-sid"
    assert mock_run.call_count == 2
    # Both calls should have --resume (retry is the same command).
    first_cmd = (
        mock_run.call_args_list[0].kwargs.get("args")
        or mock_run.call_args_list[0].args[0]
    )
    second_cmd = (
        mock_run.call_args_list[1].kwargs.get("args")
        or mock_run.call_args_list[1].args[0]
    )
    assert "--resume" in first_cmd
    assert "--resume" in second_cmd


def test_resume_transient_failure_retry_also_fails_then_fresh(tmp_path: Path) -> None:
    """Transient --resume failure -> retry -> retry also fails -> fresh session."""
    side_effects = [
        _fail(rc=1, stderr="internal error"),  # first resume attempt
        _fail(rc=1, stderr="another error"),  # retry also fails
        _ok(result="fresh-ok", sid="fresh-sid"),  # fresh session succeeds
    ]
    with patch(
        "prefect_orchestration.agent_session.subprocess.run", side_effect=side_effects
    ) as mock_run:
        with patch("prefect_orchestration.agent_session.time.sleep"):
            result, sid = ClaudeCliBackend().run(
                "hello",
                session_id=_VALID_SID,
                cwd=tmp_path,
            )
    assert result == "fresh-ok"
    assert sid == "fresh-sid"
    assert mock_run.call_count == 3
    # Third call is fresh (no --resume).
    third_cmd = (
        mock_run.call_args_list[2].kwargs.get("args")
        or mock_run.call_args_list[2].args[0]
    )
    assert "--resume" not in third_cmd


def test_fresh_session_fallback_failure_raises(tmp_path: Path) -> None:
    """If the fresh-session fallback also fails, surface as RuntimeError."""
    side_effects = [
        _fail(rc=1, stderr="No conversation found"),  # resume fails
        _fail(rc=1, stderr="fresh also broke"),  # fresh fallback also fails
    ]
    with patch(
        "prefect_orchestration.agent_session.subprocess.run", side_effect=side_effects
    ):
        with pytest.raises(RuntimeError) as excinfo:
            ClaudeCliBackend().run(
                "hello",
                session_id=_VALID_SID,
                cwd=tmp_path,
            )
    assert "fresh-session fallback" in str(excinfo.value)
    assert "fresh also broke" in str(excinfo.value)


def test_nonresume_failure_still_raises_immediately(tmp_path: Path) -> None:
    """A non-resume failure (session_id=None) still raises RuntimeError immediately."""
    with patch(
        "prefect_orchestration.agent_session.subprocess.run",
        return_value=_fail(rc=2, stderr="plain failure"),
    ) as mock_run:
        with pytest.raises(RuntimeError) as excinfo:
            ClaudeCliBackend().run(
                "hello",
                session_id=None,
                cwd=tmp_path,
            )
    # Should have called exactly once — no retry, no fallback.
    assert mock_run.call_count == 1
    assert "plain failure" in str(excinfo.value)
    assert "fresh-session" not in str(excinfo.value)
