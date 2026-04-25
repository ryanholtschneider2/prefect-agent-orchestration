"""Unit tests for `po.commands` — the pack-shipped utility-op surface.

Covers:
  * `commands.core_verbs()` matches every Typer subcommand on `cli.app`
  * `po list` shows both formulas and commands with a `KIND` column
  * `po show <name>` resolves commands as well as formulas
  * `cli.main()` dispatches `po <command>` to the registered callable
  * Argument parsing parity with `po run`
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from prefect_orchestration import cli, commands


# ---- core_verbs -------------------------------------------------------------


def test_core_verbs_includes_all_typer_subcommands() -> None:
    verbs = commands.core_verbs()
    # Sanity: every @app.command() lands in the set.
    expected = {
        "list",
        "show",
        "run",
        "deploy",
        "logs",
        "artifacts",
        "doctor",
        "status",
        "sessions",
        "retry",
        "watch",
        "install",
        "update",
        "uninstall",
        "packs",
        "tui",
    }
    missing = expected - verbs
    assert not missing, f"core_verbs missing: {missing}"


# ---- find_command_collisions -----------------------------------------------


def test_find_command_collisions_flags_core_verb_shadows() -> None:
    by_pack = {
        "pack-bad": ["run", "summarize-verdicts"],
        "pack-good": ["check-budget"],
    }
    out = commands.find_command_collisions(by_pack)
    assert out == {"pack-bad": ["run"]}


def test_find_command_collisions_empty_when_no_overlap() -> None:
    out = commands.find_command_collisions(
        {"pack-x": ["foo", "bar", "summarize-verdicts"]}
    )
    assert out == {}


# ---- po list ---------------------------------------------------------------


def _stub_command(name: str = "stubcmd", doc: str = "stub one-liner.") -> object:
    def fn(issue_id: str, verbose: bool = False) -> None:  # noqa: ARG001
        pass

    fn.__name__ = name.replace("-", "_")
    fn.__doc__ = doc
    return fn


def test_list_shows_kind_column_with_formulas_and_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fn = _stub_command("summarize-verdicts", "Summarize verdicts.")
    monkeypatch.setattr(cli, "_load_formulas", lambda: {})
    monkeypatch.setattr(commands, "load_commands", lambda: {"summarize-verdicts": fn})

    runner = CliRunner()
    result = runner.invoke(cli.app, ["list"])
    assert result.exit_code == 0, result.output
    assert "KIND" in result.output
    assert "command" in result.output
    assert "summarize-verdicts" in result.output
    assert "Summarize verdicts." in result.output


def test_list_empty_message_mentions_both_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_load_formulas", lambda: {})
    monkeypatch.setattr(commands, "load_commands", lambda: {})

    runner = CliRunner()
    result = runner.invoke(cli.app, ["list"])
    assert result.exit_code == 0
    assert "no formulas or commands installed" in result.output
    assert "po.commands" in result.output


# ---- po show ---------------------------------------------------------------


def test_show_resolves_pack_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    fn = _stub_command("summarize-verdicts", "Summarize verdicts for an issue.")
    monkeypatch.setattr(cli, "_load_formulas", lambda: {})
    monkeypatch.setattr(commands, "load_commands", lambda: {"summarize-verdicts": fn})

    runner = CliRunner()
    result = runner.invoke(cli.app, ["show", "summarize-verdicts"])
    assert result.exit_code == 0, result.output
    assert "(command)" in result.output
    assert "issue_id" in result.output  # signature
    assert "Summarize verdicts" in result.output


def test_show_unknown_name_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_load_formulas", lambda: {})
    monkeypatch.setattr(commands, "load_commands", lambda: {})

    runner = CliRunner()
    result = runner.invoke(cli.app, ["show", "nope"])
    assert result.exit_code == 1


# ---- cli.main() dispatch ---------------------------------------------------


def test_main_dispatches_pack_command(monkeypatch: pytest.MonkeyPatch) -> None:
    received: dict[str, object] = {}

    def fake(issue_id: str, verbose: bool = False) -> None:
        received["issue_id"] = issue_id
        received["verbose"] = verbose

    monkeypatch.setattr(commands, "load_commands", lambda: {"summarize-verdicts": fake})
    monkeypatch.setattr(commands, "core_verbs", lambda: {"list", "show", "run"})
    monkeypatch.setattr(
        cli.sys, "argv", ["po", "summarize-verdicts", "--issue-id=abc", "--verbose"]
    )
    cli.main()
    assert received == {"issue_id": "abc", "verbose": True}


def test_main_falls_through_to_typer_for_core_verbs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Core verbs (e.g. `po list`) must NOT be intercepted by the dispatch shim."""
    monkeypatch.setattr(commands, "load_commands", lambda: {})
    monkeypatch.setattr(cli, "_load_formulas", lambda: {})
    monkeypatch.setattr(cli.sys, "argv", ["po", "list"])
    # Typer's standalone CLI normally calls sys.exit(0) on success; that's
    # the signal that the dispatch shim handed off control rather than
    # short-circuiting.
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 0


def test_main_bad_kwargs_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    def needs_required(must_have: str) -> None:  # noqa: ARG001
        pass

    monkeypatch.setattr(commands, "load_commands", lambda: {"foo": needs_required})
    monkeypatch.setattr(commands, "core_verbs", lambda: {"list"})
    monkeypatch.setattr(cli.sys, "argv", ["po", "foo"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 2
