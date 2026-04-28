"""End-to-end secret-isolation smoke (prefect-orchestration-ddh AC4).

Two `AgentSession`s share a single `ChainSecretProvider`; the
StubBackend captures each role's `extra_env` snapshot. Asserts:

* role A's resolved env contains its own re-keyed `SLACK_TOKEN`
* role B's resolved env does NOT contain role A's token (isolation)

Uses StubBackend (no real Claude) — the role-isolation invariant is
what AC4 actually requires us to prove. A real Slack post would need
a live token + network and is gated for follow-on bead work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prefect_orchestration.agent_session import AgentSession, StubBackend
from prefect_orchestration.secrets import (
    ChainSecretProvider,
    DotenvSecretProvider,
    EnvSecretProvider,
)


def test_role_isolation_via_stub_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: planner has only an env-set token; builder has only a
    # dotenv-set token. Provider chain is dotenv-then-env.
    env_path = tmp_path / ".env"
    env_path.write_text("SLACK_TOKEN_BUILDER=xoxb-B-dotenv\n")
    monkeypatch.setenv("SLACK_TOKEN_PLANNER", "xoxb-A-env")
    monkeypatch.delenv("SLACK_TOKEN_BUILDER", raising=False)

    provider = ChainSecretProvider(
        [DotenvSecretProvider(env_path), EnvSecretProvider()]
    )

    backend = StubBackend()

    planner = AgentSession(
        role="planner",
        repo_path=tmp_path,
        backend=backend,
        secret_provider=provider,
        skip_mail_inject=True,
        overlay=False,
        skills=False,
    )
    builder = AgentSession(
        role="builder",
        repo_path=tmp_path,
        backend=backend,
        secret_provider=provider,
        skip_mail_inject=True,
        overlay=False,
        skills=False,
    )

    # Act: each role takes a turn (prompt content steers StubBackend
    # to write a triage verdict — irrelevant here, we just want the
    # extra_env capture).
    prompt = (
        "stub turn — write verdict to "
        f"cat > {tmp_path}/verdicts/triage.json <<EOF"
    )
    planner.prompt(prompt)
    builder.prompt(prompt)

    # Assert: each session's captured extra_env contains only its own token.
    captured = backend.captured_extra_env
    planner_envs = [v for k, v in captured.items() if v.get("SLACK_TOKEN") == "xoxb-A-env"]
    builder_envs = [v for k, v in captured.items() if v.get("SLACK_TOKEN") == "xoxb-B-dotenv"]
    assert planner_envs, f"planner should have its env-set SLACK_TOKEN: {captured}"
    assert builder_envs, f"builder should have its dotenv SLACK_TOKEN: {captured}"

    # Critical isolation check: scoped peer-role keys must NEVER appear
    # in either role's resolved extra_env.
    for env in captured.values():
        assert "SLACK_TOKEN_PLANNER" not in env
        assert "SLACK_TOKEN_BUILDER" not in env
