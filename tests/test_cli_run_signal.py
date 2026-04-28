"""sav.3: `po run` installs a SIGINT/SIGTERM handler that drains tmux_tracker."""

from __future__ import annotations

import signal
from typing import Any

import pytest
from typer.testing import CliRunner

from prefect_orchestration import tmux_tracker
from prefect_orchestration.cli import app
from prefect_orchestration.tmux_tracker import TmuxRef


@pytest.fixture(autouse=True)
def _clean_registry():
    tmux_tracker._LIVE.clear()
    yield
    tmux_tracker._LIVE.clear()


def test_run_installs_and_restores_signal_handlers(monkeypatch):
    """The handler installed during `flow_obj(...)` is reverted on exit."""
    captured: dict[str, Any] = {}
    sentinel_int = signal.getsignal(signal.SIGINT)
    sentinel_term = signal.getsignal(signal.SIGTERM)

    def _flow(**_kw: Any) -> str:
        captured["int_during"] = signal.getsignal(signal.SIGINT)
        captured["term_during"] = signal.getsignal(signal.SIGTERM)
        return "done"

    def _fake_load_formulas() -> dict[str, Any]:
        return {"my-flow": _flow}

    monkeypatch.setattr("prefect_orchestration.cli._load_formulas", _fake_load_formulas)
    monkeypatch.setattr(
        "prefect_orchestration.cli._autoconfigure_prefect_api", lambda: None
    )

    runner = CliRunner()
    result = runner.invoke(app, ["run", "my-flow"])
    assert result.exit_code == 0, result.output

    assert captured["int_during"] is not sentinel_int
    assert captured["term_during"] is not sentinel_term
    # Restored after the flow returns.
    assert signal.getsignal(signal.SIGINT) is sentinel_int
    assert signal.getsignal(signal.SIGTERM) is sentinel_term


def test_run_handler_kills_tmux_tracker_on_signal(monkeypatch):
    """When the installed handler fires, it drains the tmux_tracker registry."""
    killed: list[int] = []

    def _fake_kill_all() -> int:
        killed.append(len(tmux_tracker.snapshot()))
        tmux_tracker._LIVE.clear()
        return killed[-1]

    monkeypatch.setattr(tmux_tracker, "kill_all", _fake_kill_all)
    monkeypatch.setattr(
        "prefect_orchestration.cli._autoconfigure_prefect_api", lambda: None
    )

    captured_handler: dict[str, Any] = {}

    def _flow(**_kw: Any) -> str:
        # Pre-populate registry as if backends had spawned tmux sessions.
        tmux_tracker.register(TmuxRef("po-x-builder", None, "po-x-builder"))
        tmux_tracker.register(TmuxRef("po-rig", "x-tester", "@9"))
        captured_handler["h"] = signal.getsignal(signal.SIGINT)
        return "done"

    monkeypatch.setattr(
        "prefect_orchestration.cli._load_formulas", lambda: {"my-flow": _flow}
    )

    runner = CliRunner()
    result = runner.invoke(app, ["run", "my-flow"])
    assert result.exit_code == 0, result.output

    # Now exercise the handler we captured by calling it directly. typer.Exit
    # is raised; signum 2 → exit code 130.
    handler = captured_handler["h"]
    import typer

    with pytest.raises(typer.Exit) as exc_info:
        handler(signal.SIGINT, None)
    assert exc_info.value.exit_code == 128 + signal.SIGINT
    # And kill_all was invoked.
    assert killed and killed[0] == 2
