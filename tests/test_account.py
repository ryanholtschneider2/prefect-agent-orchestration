import json as _json
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
    restore_plan,
    resume_session_id,
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


def test_clean_room_account_sets_provider_and_user_homes(tmp_path: Path) -> None:
    user_home = tmp_path / "clean-user"
    config = Registry(
        accounts={
            "codex-personal2": Account(
                handle="codex-personal2",
                provider="codex",
                account_class="personal2",
                home=str(user_home / ".codex"),
                user_home=str(user_home),
            )
        },
        rules=(),
        path=tmp_path / "accounts.toml",
    )

    result = resolve_account(
        config,
        provider="codex",
        cwd=tmp_path,
        account="codex-personal2",
        environ={},
    )

    assert result.environment == {
        "HOME": str(user_home),
        "CODEX_HOME": str(user_home / ".codex"),
    }


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
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("RYAN_PERSONAL_OPENAI_KEY", "must-not-leak")

    launch_agent(
        "claude",
        ["--version"],
        cwd=cwd,
        config_path=config.path,
    )

    environment = captured["environment"]
    assert "OPENAI_API_KEY" not in environment
    assert "RYAN_PERSONAL_OPENAI_KEY" not in environment
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


# ---------- agent-restore ----------


def _restore_registry(tmp_path: Path) -> Registry:
    return Registry(
        accounts={
            "claude-personal": Account(
                "claude-personal", "claude", "personal", home=str(tmp_path / "cp")
            ),
            "claude-work": Account(
                "claude-work", "claude", "work", home=str(tmp_path / "cw")
            ),
            "codex-personal": Account(
                "codex-personal", "codex", "personal", home=str(tmp_path / "cx")
            ),
        },
        rules=(),
    )


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(_json.dumps(r) for r in records) + "\n")


def test_resume_session_id_only_matches_bare_resume() -> None:
    assert resume_session_id("claude", ["--resume", "abc"]) == "abc"
    assert resume_session_id("claude", ["-r", "abc"]) == "abc"
    assert resume_session_id("claude", ["--resume=abc"]) == "abc"
    assert resume_session_id("codex", ["resume", "abc"]) == "abc"
    assert (
        resume_session_id(
            "claude", ["--dangerously-skip-permissions", "--resume", "abc"]
        )
        == "abc"
    )
    assert resume_session_id("codex", ["--yolo", "resume", "abc"]) == "abc"
    assert resume_session_id("claude", ["--resume"]) is None
    assert resume_session_id("claude", []) is None
    assert resume_session_id("codex", ["--model", "x"]) is None


def test_restore_plan_finds_owning_claude_account_and_flags(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "cw/projects/-home-x-NuGIS/sess-1.jsonl",
        [
            {"type": "user", "permissionMode": "bypassPermissions", "effort": "medium"},
            {
                "type": "assistant",
                "message": {"model": "claude-opus-5-5"},
                "effort": "medium",
            },
        ],
    )
    plan = restore_plan(_restore_registry(tmp_path), "claude", ["--resume", "sess-1"])
    assert plan == (
        "claude-work",
        [
            "--resume",
            "sess-1",
            "--model",
            "claude-opus-5-5",
            "--effort",
            "medium",
            "--dangerously-skip-permissions",
        ],
    )


def test_restore_plan_keeps_default_permissions(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "cp/projects/p/sess-2.jsonl",
        [
            {
                "type": "assistant",
                "permissionMode": "default",
                "message": {"model": "claude-sonnet-5-5"},
            }
        ],
    )
    handle, args = restore_plan(_restore_registry(tmp_path), "claude", ["-r", "sess-2"])
    assert handle == "claude-personal"
    assert "--dangerously-skip-permissions" not in args and "claude-sonnet-5-5" in args


def test_restore_plan_codex_model_and_effort(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "cx/sessions/2026/10/27/rollout-2026-10-27T10-00-00-sess-3.jsonl",
        [
            {
                "type": "turn_context",
                "payload": {"model": "gpt-6.1-sol", "effort": "high"},
            }
        ],
    )
    plan = restore_plan(_restore_registry(tmp_path), "codex", ["resume", "sess-3"])
    assert plan == (
        "codex-personal",
        [
            "resume",
            "sess-3",
            "-m",
            "gpt-6.1-sol",
            "-c",
            'model_reasoning_effort="high"',
        ],
    )


def test_restore_plan_does_not_duplicate_flags_the_caller_set(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "cw/projects/p/sess-4.jsonl",
        [
            {
                "type": "assistant",
                "permissionMode": "bypassPermissions",
                "effort": "high",
                "message": {"model": "claude-opus-5-5"},
            }
        ],
    )
    handle, args = restore_plan(
        _restore_registry(tmp_path),
        "claude",
        ["--dangerously-skip-permissions", "--resume", "sess-4", "--model", "sonnet"],
    )
    assert handle == "claude-work"
    assert args == [
        "--dangerously-skip-permissions",
        "--resume",
        "sess-4",
        "--model",
        "sonnet",
        "--effort",
        "high",
    ]


def test_launch_agent_resume_overrides_cwd_rule(tmp_path: Path, monkeypatch) -> None:
    _write_jsonl(
        tmp_path / "cw/projects/p/sess-5.jsonl", [{"type": "user", "effort": "low"}]
    )
    config = tmp_path / "accounts.toml"
    config.write_text(
        f'''[accounts."claude-personal"]
provider = "claude"
class = "personal"
home = "{tmp_path / "cp"}"
[accounts."claude-work"]
provider = "claude"
class = "work"
home = "{tmp_path / "cw"}"
[[rules]]
path = "{tmp_path}"
class = "personal"
'''
    )
    seen = {}
    monkeypatch.setattr(
        "prefect_orchestration.account._resolve_provider_executable",
        lambda _p: "claude",
    )
    monkeypatch.setattr(
        "os.execvpe", lambda exe, argv, env: seen.update(argv=argv, env=env)
    )
    launch_agent("claude", ["--resume", "sess-5"], cwd=tmp_path, config_path=config)
    assert seen["env"]["PO_ACCOUNT"] == "claude-work"
    assert seen["env"]["CLAUDE_CONFIG_DIR"] == str((tmp_path / "cw").resolve())
    assert seen["argv"] == ["claude", "--resume", "sess-5", "--effort", "low"]
    # an explicit account still wins
    launch_agent(
        "claude",
        ["--resume", "sess-5"],
        cwd=tmp_path,
        account="claude-personal",
        config_path=config,
    )
    assert seen["env"]["PO_ACCOUNT"] == "claude-personal"


def test_restore_plan_none_when_not_a_resume_or_unknown_session(tmp_path: Path) -> None:
    reg = _restore_registry(tmp_path)
    assert restore_plan(reg, "claude", ["--model", "opus"]) is None
    assert restore_plan(reg, "claude", ["--resume", "missing"]) is None
