"""AgentSession ↔ SecretProvider wiring (prefect-orchestration-ddh AC2).

Verifies a fake backend receives the role's re-keyed secrets via
`extra_env`, and that two roles in the same flow get isolated subsets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import pytest

from prefect_orchestration.agent_session import AgentSession
from prefect_orchestration.secrets import EnvSecretProvider


@dataclass
class _CapturingBackend:
    captured: list[Mapping[str, str] | None] = field(default_factory=list)

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None,
        cwd: Path,
        fork: bool = False,
        model: str = "opus",
        effort: str | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> tuple[str, str]:
        self.captured.append(dict(extra_env) if extra_env is not None else None)
        return "ok", session_id or "sid"


def _mk_session(role: str, backend: _CapturingBackend, tmp: Path) -> AgentSession:
    return AgentSession(
        role=role,
        repo_path=tmp,
        backend=backend,
        secret_provider=EnvSecretProvider(),
        skip_mail_inject=True,
        overlay=False,
        skills=False,
    )


def test_role_a_sees_only_its_own_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLACK_TOKEN_PLANNER", "xoxb-A")
    monkeypatch.setenv("SLACK_TOKEN_BUILDER", "xoxb-B")

    backend = _CapturingBackend()
    sess = _mk_session("planner", backend, tmp_path)
    sess.prompt("hello")

    assert backend.captured == [{"SLACK_TOKEN": "xoxb-A"}]


def test_two_roles_get_isolated_envs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLACK_TOKEN_PLANNER", "xoxb-A")
    monkeypatch.setenv("SLACK_TOKEN_BUILDER", "xoxb-B")

    planner_backend = _CapturingBackend()
    builder_backend = _CapturingBackend()
    planner = _mk_session("planner", planner_backend, tmp_path)
    builder = _mk_session("builder", builder_backend, tmp_path)

    planner.prompt("p")
    builder.prompt("b")

    assert planner_backend.captured == [{"SLACK_TOKEN": "xoxb-A"}]
    assert builder_backend.captured == [{"SLACK_TOKEN": "xoxb-B"}]
    # Neither role's resolved env contains the peer's scoped key.
    assert "SLACK_TOKEN_BUILDER" not in (planner_backend.captured[0] or {})
    assert "SLACK_TOKEN_PLANNER" not in (builder_backend.captured[0] or {})


def test_no_provider_passes_none(tmp_path: Path) -> None:
    backend = _CapturingBackend()
    sess = AgentSession(
        role="planner",
        repo_path=tmp_path,
        backend=backend,
        secret_provider=None,
        skip_mail_inject=True,
        overlay=False,
        skills=False,
    )
    sess.prompt("hello")
    assert backend.captured == [None]


def test_role_with_no_secret_gets_empty_dict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SLACK_TOKEN_VERIFIER", raising=False)
    backend = _CapturingBackend()
    sess = _mk_session("verifier", backend, tmp_path)
    sess.prompt("hello")
    assert backend.captured == [{}]


def test_role_normalization_hyphen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLACK_TOKEN_PLAN_CRITIC", "xoxb-pc")
    backend = _CapturingBackend()
    sess = _mk_session("plan-critic", backend, tmp_path)
    sess.prompt("hello")
    assert backend.captured == [{"SLACK_TOKEN": "xoxb-pc"}]
