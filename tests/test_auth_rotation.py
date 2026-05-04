from __future__ import annotations

from pathlib import Path

from prefect_orchestration import auth_rotation


def test_rotate_to_next_oauth_pool_slot_advances_env_token(
    tmp_path: Path, monkeypatch
) -> None:
    token_file = tmp_path / "claude_oauth.txt"
    token_file.write_text("# comment\ntok-0\n\ntok-1\n")

    monkeypatch.setenv("PO_CLAUDE_OAUTH_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PO_CLAUDE_OAUTH_TOKEN_INDEX", "0")
    monkeypatch.setenv("PO_CLAUDE_OAUTH_TOKEN_COUNT", "2")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok-0")

    idx = auth_rotation.rotate_to_next_oauth_pool_slot()

    assert idx == 1
    assert auth_rotation.oauth_failover_budget() == 1
    assert auth_rotation.oauth_token_file() == token_file
    assert auth_rotation.os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-1"


def test_rotate_to_next_oauth_pool_slot_returns_none_without_pool(
    tmp_path: Path, monkeypatch
) -> None:
    token_file = tmp_path / "claude_oauth.txt"
    token_file.write_text("tok-0\n")
    monkeypatch.setenv("PO_CLAUDE_OAUTH_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PO_CLAUDE_OAUTH_TOKEN_INDEX", "0")
    monkeypatch.setenv("PO_CLAUDE_OAUTH_TOKEN_COUNT", "1")

    assert auth_rotation.rotate_to_next_oauth_pool_slot() is None


def test_rotate_wraps_around_to_first_slot(tmp_path: Path, monkeypatch) -> None:
    token_file = tmp_path / "tokens.txt"
    token_file.write_text("tok-a\ntok-b\ntok-c\n")
    monkeypatch.setenv("PO_CLAUDE_OAUTH_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PO_CLAUDE_OAUTH_TOKEN_INDEX", "2")
    monkeypatch.setenv("PO_CLAUDE_OAUTH_TOKEN_COUNT", "3")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok-c")

    idx = auth_rotation.rotate_to_next_oauth_pool_slot()

    assert idx == 0
    assert auth_rotation.os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-a"
    assert auth_rotation.os.environ[auth_rotation.TOKEN_INDEX_ENV] == "0"


def test_oauth_token_count_returns_zero_for_invalid_env(monkeypatch) -> None:
    monkeypatch.setenv("PO_CLAUDE_OAUTH_TOKEN_COUNT", "not-a-number")
    assert auth_rotation.oauth_token_count() == 0


def test_oauth_failover_budget_is_count_minus_one(monkeypatch) -> None:
    monkeypatch.setenv("PO_CLAUDE_OAUTH_TOKEN_COUNT", "5")
    assert auth_rotation.oauth_failover_budget() == 4


def test_oauth_failover_budget_zero_when_no_pool(monkeypatch) -> None:
    monkeypatch.delenv("PO_CLAUDE_OAUTH_TOKEN_COUNT", raising=False)
    assert auth_rotation.oauth_failover_budget() == 0


def test_rotate_returns_none_when_token_file_missing(monkeypatch) -> None:
    monkeypatch.setenv("PO_CLAUDE_OAUTH_TOKEN_FILE", "/nonexistent/file.txt")
    monkeypatch.setenv("PO_CLAUDE_OAUTH_TOKEN_INDEX", "0")
    monkeypatch.setenv("PO_CLAUDE_OAUTH_TOKEN_COUNT", "2")

    assert auth_rotation.rotate_to_next_oauth_pool_slot() is None


def test_rotate_returns_none_when_no_token_file_env(monkeypatch) -> None:
    monkeypatch.delenv("PO_CLAUDE_OAUTH_TOKEN_FILE", raising=False)
    monkeypatch.setenv("PO_CLAUDE_OAUTH_TOKEN_INDEX", "0")
    monkeypatch.setenv("PO_CLAUDE_OAUTH_TOKEN_COUNT", "2")

    assert auth_rotation.rotate_to_next_oauth_pool_slot() is None
