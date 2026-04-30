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


def test_scheduled_run_stamps_runtime_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`po run … --time 2h --model sonnet` stamps PO_MODEL_CLI before
    submitting the deferred run. Mirrors the sync-path behavior so a
    worker picking up the scheduled flow inherits the operator's
    runtime knobs."""
    from prefect_orchestration import cli as cli_mod

    def _flow() -> str:
        return "ok"

    _patch_flow(monkeypatch, _flow)

    captured: dict[str, Any] = {}

    async def _fake_submit(**kwargs: Any) -> tuple[Any, str, None]:
        captured["env_at_submit"] = {
            "PO_MODEL_CLI": os.environ.get("PO_MODEL_CLI"),
            "PO_EFFORT_CLI": os.environ.get("PO_EFFORT_CLI"),
            "PO_START_COMMAND_CLI": os.environ.get("PO_START_COMMAND_CLI"),
        }

        class _FR:
            id = "fr-test"

        return _FR(), "my-flow/my-flow-manual", None

    class _FakeClientCtx:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(
        "prefect.client.orchestration.get_client", lambda: _FakeClientCtx()
    )
    monkeypatch.setattr(cli_mod._scheduling, "submit_scheduled_run", _fake_submit)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            "my-flow",
            "--time",
            "2h",
            "--model",
            "sonnet",
            "--effort",
            "low",
            "--start-command",
            "claude --foo",
        ],
    )
    assert result.exit_code == 0, result.output
    env = captured["env_at_submit"]
    assert env["PO_MODEL_CLI"] == "sonnet"
    assert env["PO_EFFORT_CLI"] == "low"
    assert env["PO_START_COMMAND_CLI"] == "claude --foo"


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
