"""Regression tests for ClaudeCliBackend error reporting (issue 7hv)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from prefect_orchestration.agent_session import ClaudeCliBackend


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
    with patch("prefect_orchestration.agent_session.subprocess.run", return_value=completed):
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
    with patch("prefect_orchestration.agent_session.subprocess.run", return_value=completed):
        result, sid = ClaudeCliBackend().run(
            "hello",
            session_id=None,
            cwd=tmp_path,
        )
    assert result == "ok"
    assert sid == "sid-123"
