from __future__ import annotations

import json

from typer.testing import CliRunner

from prefect_orchestration.cli import _stamp_dispatch_manifest, app


def test_registered_formula_submits_durably_by_default(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def flow(issue_id: str = "") -> str:
        raise AssertionError("default dispatch must not execute in the caller")

    def submit(**kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(
        "prefect_orchestration.cli._load_formulas", lambda: {"demo": flow}
    )
    monkeypatch.setattr("prefect_orchestration.cli._run_scheduled", submit)
    monkeypatch.setattr(
        "prefect_orchestration.cli._autoconfigure_prefect_api", lambda: None
    )

    result = CliRunner().invoke(app, ["run", "demo", "--issue-id", "demo-1"])

    assert result.exit_code == 0, result.output
    assert captured["name"] == "demo"
    assert captured["when"] is None


def test_foreground_opt_in_executes_in_caller(monkeypatch) -> None:
    called: list[str] = []

    def flow() -> str:
        called.append("yes")
        return "done"

    monkeypatch.setattr(
        "prefect_orchestration.cli._load_formulas", lambda: {"demo": flow}
    )
    monkeypatch.setattr(
        "prefect_orchestration.cli._autoconfigure_prefect_api", lambda: None
    )

    result = CliRunner().invoke(app, ["run", "demo", "--foreground"])

    assert result.exit_code == 0, result.output
    assert called == ["yes"]


def test_dispatch_manifest_preserves_runtime_tuple(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PO_BACKEND", "codex")
    monkeypatch.setenv("PO_ACCOUNT", "personal")
    monkeypatch.setenv("PO_MODEL_CLI", "gpt-5.4")

    _stamp_dispatch_manifest(
        "demo",
        {"issue_id": "demo-1", "rig_path": str(tmp_path), "other": 42},
    )

    manifest_path = tmp_path / ".planning/demo/demo-1/.po-dispatch.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["runtime_env"]["PO_ACCOUNT"] == "personal"
    assert manifest["runtime_env"]["PO_BACKEND"] == "codex"
    assert manifest["runtime_env"]["PO_MODEL_CLI"] == "gpt-5.4"
    assert (manifest_path.parent / ".po-formula").read_text() == "demo"
