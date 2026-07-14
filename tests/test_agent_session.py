"""Regression tests for CLI backend error reporting and parsing."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from prefect_orchestration.agent_session import (
    AgentSession,
    ClaudeCliBackend,
    CodexCliBackend,
    CursorCliBackend,
    ModelCapacityError,
    RuntimeFallback,
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


@pytest.mark.parametrize(
    ("backend", "transcript", "provider"),
    [
        (
            ClaudeCliBackend(),
            '{"type":"error","error":{"type":"overloaded_error"}}',
            "claude",
        ),
        (
            CodexCliBackend(),
            '{"type":"error","message":"Model capacity is temporarily unavailable"}',
            "codex",
        ),
        (
            CursorCliBackend(),
            '{"type":"error","message":"Model is unavailable due to high demand"}',
            "cursor",
        ),
    ],
)
def test_explicit_capacity_transcripts_are_typed(
    tmp_path: Path, backend: object, transcript: str, provider: str
) -> None:
    completed = subprocess.CompletedProcess(
        args=[provider], returncode=1, stdout=transcript, stderr=""
    )
    with patch(
        "prefect_orchestration.agent_session.subprocess.run", return_value=completed
    ):
        with pytest.raises(ModelCapacityError) as excinfo:
            backend.run("hello", session_id=None, cwd=tmp_path)  # type: ignore[attr-defined]
    assert excinfo.value.provider == provider


def test_ordinary_failure_that_mentions_model_does_not_fail_over(
    tmp_path: Path,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["codex"],
        returncode=1,
        stdout="tests failed: model validation assertion mismatch",
        stderr="",
    )
    with patch(
        "prefect_orchestration.agent_session.subprocess.run", return_value=completed
    ):
        with pytest.raises(RuntimeError) as excinfo:
            CodexCliBackend().run("hello", session_id=None, cwd=tmp_path)
    assert type(excinfo.value) is RuntimeError


class _ScriptedBackend:
    def __init__(self, outcomes: list[object]):
        self.outcomes = outcomes
        self.calls = 0

    def run(self, *args, **kwargs):
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def test_explicit_capacity_retry_then_runtime_fallback_preserves_provenance(
    tmp_path: Path,
) -> None:
    primary = _ScriptedBackend(
        [
            ModelCapacityError("codex", "gpt-primary", "capacity exhausted"),
            ModelCapacityError("codex", "gpt-primary", "capacity exhausted"),
        ]
    )
    fallback = _ScriptedBackend([("done", "fallback-session")])
    session = AgentSession(
        role="builder",
        repo_path=tmp_path,
        backend=primary,
        model="gpt-primary",
        overlay=False,
        skills=False,
        capacity_retries=1,
        runtime_fallbacks=(
            RuntimeFallback(
                fallback,
                "gpt-fallback",
                "high",
                "operator-secondary",
                account="codex-personal",
                account_class="personal",
            ),
        ),
    )

    assert session.prompt("build") == "done"
    assert primary.calls == 2
    assert fallback.calls == 1
    assert session.backend is fallback
    assert session.model == "gpt-fallback"
    assert [row["outcome"] for row in session.last_runtime_provenance] == [
        "capacity-exhausted",
        "capacity-exhausted",
        "completed",
    ]
    assert session.last_runtime_provenance[-1]["account"] == "codex-personal"
    assert session.last_runtime_provenance[-1]["account_class"] == "personal"


def test_no_implicit_fallback_and_no_failover_on_ordinary_failure(
    tmp_path: Path,
) -> None:
    capacity = _ScriptedBackend(
        [ModelCapacityError("codex", "gpt-primary", "capacity exhausted")]
    )
    session = AgentSession(
        role="builder",
        repo_path=tmp_path,
        backend=capacity,
        model="gpt-primary",
        overlay=False,
        skills=False,
    )
    with pytest.raises(ModelCapacityError):
        session.prompt("build")
    assert capacity.calls == 1

    primary = _ScriptedBackend([RuntimeError("pytest failed")])
    fallback = _ScriptedBackend([("must-not-run", "sid")])
    session = AgentSession(
        role="builder",
        repo_path=tmp_path,
        backend=primary,
        overlay=False,
        skills=False,
        runtime_fallbacks=(RuntimeFallback(fallback, "other"),),
    )
    with pytest.raises(RuntimeError, match="pytest failed"):
        session.prompt("build")
    assert fallback.calls == 0
