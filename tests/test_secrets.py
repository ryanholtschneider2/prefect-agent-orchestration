"""Unit tests for prefect_orchestration.secrets.

Covers prefect-orchestration-ddh AC1: SecretProvider Protocol +
EnvSecretProvider + DotenvSecretProvider behavior, role-key
normalization, peer-role leakage scrub.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prefect_orchestration.secrets import (
    DEFAULT_PREFIXES,
    ChainSecretProvider,
    DotenvSecretProvider,
    EnvSecretProvider,
    SecretProvider,
    _parse_dotenv,
    resolve_role_env,
    role_env_key,
    strip_role_scoped,
)


def test_role_env_key_simple() -> None:
    assert role_env_key("planner") == "PLANNER"


def test_role_env_key_hyphen_dot_space() -> None:
    assert role_env_key("plan-critic") == "PLAN_CRITIC"
    assert role_env_key("prefect-orchestration-4ja.1") == "PREFECT_ORCHESTRATION_4JA_1"
    assert role_env_key("acquisitions bot") == "ACQUISITIONS_BOT"


def test_env_provider_runtime_checkable() -> None:
    assert isinstance(EnvSecretProvider(), SecretProvider)
    assert isinstance(DotenvSecretProvider(Path("/dev/null")), SecretProvider)


def test_env_provider_rekeys_role_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_TOKEN_PLANNER", "xoxb-A")
    monkeypatch.setenv("SLACK_TOKEN_BUILDER", "xoxb-B")
    monkeypatch.setenv("GMAIL_CREDS_PLANNER", "creds-A")
    p = EnvSecretProvider()
    assert p.get_role_env("planner") == {
        "SLACK_TOKEN": "xoxb-A",
        "GMAIL_CREDS": "creds-A",
    }
    assert p.get_role_env("builder") == {"SLACK_TOKEN": "xoxb-B"}


def test_env_provider_normalizes_role_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_TOKEN_PLAN_CRITIC", "xoxb-pc")
    p = EnvSecretProvider()
    assert p.get_role_env("plan-critic") == {"SLACK_TOKEN": "xoxb-pc"}
    assert p.get_role_env("plan.critic") == {"SLACK_TOKEN": "xoxb-pc"}


def test_env_provider_unknown_role_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_TOKEN_PLANNER", "xoxb-A")
    p = EnvSecretProvider()
    assert p.get_role_env("verifier") == {}


def test_env_provider_skips_empty_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_TOKEN_PLANNER", "")
    p = EnvSecretProvider()
    assert "SLACK_TOKEN" not in p.get_role_env("planner")


def test_env_provider_custom_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRIPE_KEY_BILLING_BOT", "sk_test")
    p = EnvSecretProvider(prefixes=("STRIPE_KEY",))
    assert p.get_role_env("billing-bot") == {"STRIPE_KEY": "sk_test"}


def test_dotenv_parser_quotes_and_comments() -> None:
    text = '\n'.join(
        [
            "# a comment",
            "",
            "SLACK_TOKEN_PLANNER=xoxb-A",
            'SLACK_TOKEN_BUILDER="xoxb with spaces"',
            "GMAIL_CREDS_PLANNER='single-quoted'",
            "export ATTIO_TOKEN_PLANNER=at_123",
            "BAD LINE WITHOUT EQUALS",
        ]
    )
    parsed = _parse_dotenv(text)
    assert parsed == {
        "SLACK_TOKEN_PLANNER": "xoxb-A",
        "SLACK_TOKEN_BUILDER": "xoxb with spaces",
        "GMAIL_CREDS_PLANNER": "single-quoted",
        "ATTIO_TOKEN_PLANNER": "at_123",
    }


def test_dotenv_provider_reads_file(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("SLACK_TOKEN_PLANNER=xoxb-DOT\nGMAIL_CREDS_BUILDER=creds-DOT\n")
    p = DotenvSecretProvider(env_path)
    assert p.get_role_env("planner") == {"SLACK_TOKEN": "xoxb-DOT"}
    assert p.get_role_env("builder") == {"GMAIL_CREDS": "creds-DOT"}


def test_dotenv_provider_missing_file(tmp_path: Path) -> None:
    p = DotenvSecretProvider(tmp_path / "absent.env")
    assert p.get_role_env("planner") == {}


def test_chain_first_hit_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("SLACK_TOKEN_PLANNER=from-dotenv\n")
    monkeypatch.setenv("SLACK_TOKEN_PLANNER", "from-process")
    monkeypatch.setenv("GMAIL_CREDS_PLANNER", "gmail-from-process")
    chain = ChainSecretProvider(
        [DotenvSecretProvider(env_path), EnvSecretProvider()]
    )
    out = chain.get_role_env("planner")
    assert out["SLACK_TOKEN"] == "from-dotenv"  # dotenv wins
    assert out["GMAIL_CREDS"] == "gmail-from-process"  # env fills the gap


def test_strip_role_scoped_removes_all_prefixed() -> None:
    env = {
        "SLACK_TOKEN_PLANNER": "a",
        "SLACK_TOKEN_BUILDER": "b",
        "GMAIL_CREDS_PLANNER": "c",
        "PATH": "/usr/bin",
        "SLACK_TOKEN": "should-stay",  # base key (no role suffix) survives
    }
    strip_role_scoped(env, DEFAULT_PREFIXES)
    assert "SLACK_TOKEN_PLANNER" not in env
    assert "SLACK_TOKEN_BUILDER" not in env
    assert "GMAIL_CREDS_PLANNER" not in env
    assert env["PATH"] == "/usr/bin"
    assert env["SLACK_TOKEN"] == "should-stay"


def test_resolve_role_env_strips_then_overlays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = {
        "PATH": "/usr/bin",
        "SLACK_TOKEN_PLANNER": "xoxb-A",
        "SLACK_TOKEN_BUILDER": "xoxb-B",
    }
    monkeypatch.setenv("SLACK_TOKEN_PLANNER", "xoxb-A")
    monkeypatch.setenv("SLACK_TOKEN_BUILDER", "xoxb-B")
    out = resolve_role_env("planner", base_env=base, provider=EnvSecretProvider())
    assert out["PATH"] == "/usr/bin"
    assert out["SLACK_TOKEN"] == "xoxb-A"
    # Critical: peer-role secret nowhere in resolved env.
    assert "SLACK_TOKEN_BUILDER" not in out
    assert "SLACK_TOKEN_PLANNER" not in out


def test_resolve_role_env_no_provider_still_strips() -> None:
    base = {"PATH": "/usr/bin", "SLACK_TOKEN_PLANNER": "x"}
    out = resolve_role_env("planner", base_env=base, provider=None)
    assert "SLACK_TOKEN_PLANNER" not in out
    assert "SLACK_TOKEN" not in out
    assert out["PATH"] == "/usr/bin"


def test_provider_repr_does_not_leak_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_TOKEN_PLANNER", "xoxb-SECRET-LEAK")
    p = EnvSecretProvider()
    assert "xoxb-SECRET-LEAK" not in repr(p)
