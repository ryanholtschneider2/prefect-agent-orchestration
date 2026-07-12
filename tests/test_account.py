from pathlib import Path

import pytest

from prefect_orchestration.account import (
    Account,
    AccountError,
    DirectoryRule,
    Registry,
    load_registry,
    launch_agent,
    resolve_account,
    resolve_environment_for_backend,
    save_registry,
    sync_shared_config,
)
from prefect_orchestration.agent_session import CursorCliBackend


def registry(tmp_path: Path) -> Registry:
    return Registry(
        accounts={
            "codex-personal": Account(
                handle="codex-personal",
                provider="codex",
                account_class="personal",
                home="~/.codex",
            ),
            "claude-personal": Account(
                handle="claude-personal",
                provider="claude",
                account_class="personal",
                home="~/.claude-accounts/personal",
            ),
            "claude-work": Account(
                handle="claude-work",
                provider="claude",
                account_class="work",
                home="~/.claude-accounts/work",
            ),
        },
        rules=(
            DirectoryRule(path=str(tmp_path / "personal"), account_class="personal"),
            DirectoryRule(path=str(tmp_path / "work"), account_class="work"),
        ),
        path=tmp_path / "accounts.toml",
    )


def test_save_and_load_registry_round_trip(tmp_path: Path) -> None:
    expected = registry(tmp_path)
    save_registry(expected)

    loaded = load_registry(expected.path)

    assert loaded.accounts == expected.accounts
    assert loaded.rules == expected.rules
    assert expected.path.stat().st_mode & 0o777 == 0o600


def test_resolve_uses_longest_directory_rule(tmp_path: Path) -> None:
    config = registry(tmp_path)
    cwd = tmp_path / "work" / "repo"
    cwd.mkdir(parents=True)

    result = resolve_account(config, provider="claude-code", cwd=cwd, environ={})

    assert result.handle == "claude-work"
    assert result.source == "directory-rule"
    assert result.environment == {
        "CLAUDE_CONFIG_DIR": str(Path("~/.claude-accounts/work").expanduser())
    }


def test_explicit_handle_can_override_directory_class(tmp_path: Path) -> None:
    config = registry(tmp_path)
    cwd = tmp_path / "work" / "repo"
    cwd.mkdir(parents=True)

    result = resolve_account(
        config,
        provider="claude",
        cwd=cwd,
        account="claude-personal",
        environ={},
    )

    assert result.handle == "claude-personal"
    assert result.source == "explicit-account"


def test_class_conflict_fails_closed(tmp_path: Path) -> None:
    config = registry(tmp_path)
    cwd = tmp_path / "work" / "repo"
    cwd.mkdir(parents=True)

    with pytest.raises(AccountError, match="conflicts with directory rule"):
        resolve_account(
            config,
            provider="claude",
            cwd=cwd,
            account_class="personal",
            environ={},
        )


def test_unique_provider_account_is_fallback(tmp_path: Path) -> None:
    result = resolve_account(
        registry(tmp_path),
        provider="codex",
        cwd=tmp_path,
        environ={},
    )

    assert result.handle == "codex-personal"
    assert result.source == "unique-provider-account"
    assert result.environment == {"CODEX_HOME": str(Path("~/.codex").expanduser())}


def test_launch_agent_execs_cursor_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Registry(
        accounts={
            "cursor-personal": Account(
                handle="cursor-personal",
                provider="cursor",
                account_class="personal",
            ),
        },
        rules=(),
        path=tmp_path / "accounts.toml",
    )
    save_registry(config)
    captured: dict[str, object] = {}

    def which_side_effect(executable: str) -> str | None:
        if executable == "cursor-agent":
            return "/usr/bin/cursor-agent"
        return None

    monkeypatch.setattr(
        "prefect_orchestration.account.shutil.which",
        which_side_effect,
    )

    def capture_exec(
        executable: str, argv: list[str], environment: dict[str, str]
    ) -> None:
        captured.update(
            executable=executable,
            argv=argv,
            environment=environment,
        )

    monkeypatch.setattr("prefect_orchestration.account.os.execvpe", capture_exec)
    monkeypatch.delenv("PO_ACCOUNT", raising=False)
    monkeypatch.delenv("PO_ACCOUNT_CLASS", raising=False)

    launch_agent(
        "cursor",
        ["--version"],
        cwd=tmp_path,
        config_path=config.path,
    )

    assert captured["executable"] == "/usr/bin/cursor-agent"
    assert captured["argv"] == ["/usr/bin/cursor-agent", "--version"]
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["PO_ACCOUNT"] == "cursor-personal"
    assert environment["PO_ACCOUNT_CLASS"] == "personal"


def test_cursor_backend_resolves_cursor_account(tmp_path: Path) -> None:
    config = Registry(
        accounts={
            "cursor-personal": Account(
                handle="cursor-personal",
                provider="cursor",
                account_class="personal",
            ),
        },
        rules=(),
        path=tmp_path / "accounts.toml",
    )
    save_registry(config)

    resolution = resolve_environment_for_backend(
        CursorCliBackend(),
        cwd=tmp_path,
        account="cursor-personal",
        config_path=config.path,
    )

    assert resolution is not None
    assert resolution.handle == "cursor-personal"
    assert resolution.provider == "cursor"


def test_launch_agent_execs_provider_with_resolved_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = registry(tmp_path)
    save_registry(config)
    cwd = tmp_path / "work" / "repo"
    cwd.mkdir(parents=True)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "prefect_orchestration.account.shutil.which",
        lambda executable: f"/usr/bin/{executable}",
    )

    def capture_exec(
        executable: str, argv: list[str], environment: dict[str, str]
    ) -> None:
        captured.update(
            executable=executable,
            argv=argv,
            environment=environment,
        )

    monkeypatch.setattr("prefect_orchestration.account.os.execvpe", capture_exec)
    monkeypatch.setenv("PO_ACCOUNT", "")
    monkeypatch.setenv("PO_ACCOUNT_CLASS", "")

    launch_agent(
        "claude",
        ["--version"],
        cwd=cwd,
        config_path=config.path,
    )

    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert captured["executable"] == "/usr/bin/claude"
    assert captured["argv"] == ["/usr/bin/claude", "--version"]
    assert environment["CLAUDE_CONFIG_DIR"] == str(
        Path("~/.claude-accounts/work").expanduser()
    )
    assert environment["PO_ACCOUNT"] == "claude-work"
    assert environment["PO_ACCOUNT_CLASS"] == "work"


def test_sync_shared_config_links_static_config_and_preserves_credentials(
    tmp_path: Path,
) -> None:
    personal_home = tmp_path / "claude-personal"
    work_home = tmp_path / "claude-work"
    personal_home.mkdir()
    work_home.mkdir()
    (personal_home / "commands").mkdir()
    (personal_home / "commands" / "review.md").write_text("review")
    (personal_home / "settings.json").write_text('{"theme": "dark"}')
    (personal_home / ".credentials.json").write_text("personal")
    (work_home / ".credentials.json").write_text("work")
    (work_home / "settings.json").write_text('{"theme": "light"}')
    config = Registry(
        accounts={
            "claude-personal": Account(
                handle="claude-personal",
                provider="claude",
                account_class="personal",
                home=str(personal_home),
            ),
            "claude-work": Account(
                handle="claude-work",
                provider="claude",
                account_class="work",
                home=str(work_home),
                config_source="claude-personal",
            ),
        },
        rules=(),
        path=tmp_path / "accounts.toml",
    )

    links = sync_shared_config(config)

    assert (work_home / "commands").resolve() == personal_home / "commands"
    assert (work_home / "settings.json").resolve() == personal_home / "settings.json"
    assert (work_home / ".credentials.json").read_text() == "work"
    assert any(target.name == "commands" for target, _ in links)
    backups = list((work_home / "backups").glob("shared-config-*/settings.json"))
    assert len(backups) == 1
    assert backups[0].read_text() == '{"theme": "light"}'


def test_sync_shared_config_links_cursor_cli_files(tmp_path: Path) -> None:
    personal_home = tmp_path / "cursor-personal"
    work_home = tmp_path / "cursor-work"
    personal_home.mkdir()
    work_home.mkdir()
    (personal_home / "cli-config.json").write_text('{"model":"composer-2.5"}')
    (personal_home / "mcp.json").write_text('{"mcpServers":{}}')
    (work_home / "cli-config.json").write_text('{"model":"gpt-5.4"}')
    (work_home / "mcp.json").write_text('{"mcpServers":{"local":{}}}')
    config = Registry(
        accounts={
            "cursor-personal": Account(
                handle="cursor-personal",
                provider="cursor",
                account_class="personal",
                home=str(personal_home),
            ),
            "cursor-work": Account(
                handle="cursor-work",
                provider="cursor",
                account_class="work",
                home=str(work_home),
                config_source="cursor-personal",
            ),
        },
        rules=(),
        path=tmp_path / "accounts.toml",
    )

    links = sync_shared_config(config)

    assert (
        work_home / "cli-config.json"
    ).resolve() == personal_home / "cli-config.json"
    assert (work_home / "mcp.json").resolve() == personal_home / "mcp.json"
    assert any(target.name == "cli-config.json" for target, _ in links)
