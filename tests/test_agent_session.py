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
