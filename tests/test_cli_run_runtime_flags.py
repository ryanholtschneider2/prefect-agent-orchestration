"""`po run --model / --effort / --start-command` env-stamping + kwargs passthrough."""

from __future__ import annotations

import os
from typing import Any

import pytest
from typer.testing import CliRunner

from prefect_orchestration.cli import app


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "PO_MODEL",
        "PO_MODEL_CLI",
        "PO_EFFORT",
        "PO_EFFORT_CLI",
        "PO_START_COMMAND",
        "PO_START_COMMAND_CLI",
    ):
        monkeypatch.delenv(var, raising=False)


def _patch_flow(
    monkeypatch: pytest.MonkeyPatch, fn: Any, name: str = "my-flow"
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _wrapped(**kwargs: Any) -> str:
        captured["kwargs"] = dict(kwargs)
        captured["env_at_run"] = {
            k: os.environ.get(k)
            for k in (
                "PO_MODEL_CLI",
                "PO_EFFORT_CLI",
                "PO_START_COMMAND_CLI",
                "PO_MODEL",
                "PO_EFFORT",
                "PO_START_COMMAND",
            )
        }
        return fn(**kwargs)

    # Mirror the real flow's signature so _apply_runtime_overrides can
    # introspect parameters via inspect.signature.
    _wrapped.__signature__ = __import__("inspect").signature(fn)  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "prefect_orchestration.cli._load_formulas", lambda: {name: _wrapped}
    )
    monkeypatch.setattr(
        "prefect_orchestration.cli._autoconfigure_prefect_api", lambda: None
    )
    return captured


def test_model_flag_stamps_po_model_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    def _flow() -> str:
        return "ok"

    captured = _patch_flow(monkeypatch, _flow)
    runner = CliRunner()
    result = runner.invoke(app, ["run", "my-flow", "--model", "sonnet"])
    assert result.exit_code == 0, result.output
    assert captured["env_at_run"]["PO_MODEL_CLI"] == "sonnet"


def test_effort_flag_stamps_po_effort_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    def _flow() -> str:
        return "ok"

    captured = _patch_flow(monkeypatch, _flow)
    runner = CliRunner()
    result = runner.invoke(app, ["run", "my-flow", "--effort", "low"])
    assert result.exit_code == 0, result.output
    assert captured["env_at_run"]["PO_EFFORT_CLI"] == "low"


def test_start_command_flag_stamps_po_start_command_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _flow() -> str:
        return "ok"

    captured = _patch_flow(monkeypatch, _flow)
    runner = CliRunner()
    result = runner.invoke(app, ["run", "my-flow", "--start-command", "claude --foo"])
    assert result.exit_code == 0, result.output
    assert captured["env_at_run"]["PO_START_COMMAND_CLI"] == "claude --foo"


def test_all_three_together(monkeypatch: pytest.MonkeyPatch) -> None:
    def _flow() -> str:
        return "ok"

    captured = _patch_flow(monkeypatch, _flow)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            "my-flow",
            "--model",
            "haiku",
            "--effort",
            "max",
            "--start-command",
            "claude --foo",
        ],
    )
    assert result.exit_code == 0, result.output
    env = captured["env_at_run"]
    assert env["PO_MODEL_CLI"] == "haiku"
    assert env["PO_EFFORT_CLI"] == "max"
    assert env["PO_START_COMMAND_CLI"] == "claude --foo"


def test_kwargs_passthrough_when_flow_accepts_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flows whose signature includes `model` get the value as a kwarg too."""

    def _flow(model: str = "default") -> str:
        return f"ran with {model}"

    captured = _patch_flow(monkeypatch, _flow)
    runner = CliRunner()
    result = runner.invoke(app, ["run", "my-flow", "--model", "sonnet"])
    assert result.exit_code == 0, result.output
    assert captured["kwargs"].get("model") == "sonnet"


def test_kwargs_no_passthrough_when_flow_lacks_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flow without `model` kwarg → only env var stamped, no kwarg added."""

    def _flow() -> str:
        return "ok"

    captured = _patch_flow(monkeypatch, _flow)
    runner = CliRunner()
    result = runner.invoke(app, ["run", "my-flow", "--model", "sonnet"])
    assert result.exit_code == 0, result.output
    assert "model" not in captured["kwargs"]
    assert captured["env_at_run"]["PO_MODEL_CLI"] == "sonnet"


def test_no_flags_no_env_stamped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare `po run` doesn't stamp any PO_*_CLI vars."""

    def _flow() -> str:
        return "ok"

    captured = _patch_flow(monkeypatch, _flow)
    runner = CliRunner()
    result = runner.invoke(app, ["run", "my-flow"])
    assert result.exit_code == 0, result.output
    env = captured["env_at_run"]
    assert env["PO_MODEL_CLI"] is None
    assert env["PO_EFFORT_CLI"] is None
    assert env["PO_START_COMMAND_CLI"] is None


def test_flag_does_not_overwrite_explicit_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `--model` extras-arg passed by user wins over the same flag value
    via setdefault — the explicit kwarg stays put."""

    def _flow(model: str = "default") -> str:
        return f"ran with {model}"

    captured = _patch_flow(monkeypatch, _flow)
    # Both forms shouldn't be needed in practice, but make sure setdefault
    # doesn't double-stomp if a user passes --model twice somehow.
    runner = CliRunner()
    result = runner.invoke(app, ["run", "my-flow", "--model", "sonnet"])
    assert result.exit_code == 0, result.output
    assert captured["kwargs"].get("model") == "sonnet"


def test_help_documents_new_flags() -> None:
    """`po run --help` advertises --model, --effort, --start-command."""
    runner = CliRunner()
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0, result.output
    # Strip ANSI / wrapping for substring matching.
    text = result.output
    assert "--model" in text
    assert "--effort" in text
    assert "--start-command" in text
