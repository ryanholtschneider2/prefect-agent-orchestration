"""CLI-level tests for the pack lifecycle verbs."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prefect_orchestration import packs
from prefect_orchestration.cli import app


runner = CliRunner()


def _fake_ok(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    called: list[list[str]] = []

    def fake(args: list[str]) -> subprocess.CompletedProcess[str]:
        called.append(args)
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(packs, "_run_uv", fake)
    return called


def test_install_cli_passes_spec_to_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    called = _fake_ok(monkeypatch)
    result = runner.invoke(app, ["install", "po-formulas-software-dev"])
    assert result.exit_code == 0, result.output
    assert "installed po-formulas-software-dev" in result.output
    assert called and called[0][-1] == "po-formulas-software-dev"
    assert "--with" in called[0]


def test_install_editable_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    called = _fake_ok(monkeypatch)
    result = runner.invoke(app, ["install", "--editable", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert called[0][-2] == "--with-editable"
    assert called[0][-1] == str(tmp_path)


def test_update_all(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_ok(monkeypatch)
    pi = packs.PackInfo(
        name="po-formulas-x",
        version="0.1",
        source="pypi",
        contributions={"po.formulas": ["y"]},
    )
    monkeypatch.setattr(packs, "discover_packs", lambda: [pi])
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0, result.output
    assert "po-formulas-x" in result.output


def test_uninstall_refuses_self(monkeypatch: pytest.MonkeyPatch) -> None:
    result = runner.invoke(app, ["uninstall", packs.CORE_DISTRIBUTION])
    assert result.exit_code == 2
    assert "refusing" in result.output


def test_uninstall_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_ok(monkeypatch)
    monkeypatch.setattr(packs, "discover_packs", lambda: [])
    result = runner.invoke(app, ["uninstall", "po-formulas-foo"])
    assert result.exit_code == 0, result.output
    assert "uninstalled po-formulas-foo" in result.output


def test_packs_lists_contributions(monkeypatch: pytest.MonkeyPatch) -> None:
    pi = packs.PackInfo(
        name="po-formulas-x",
        version="0.1",
        source="pypi",
        contributions={"po.formulas": ["flow-a"], "po.commands": ["cmd-b"]},
    )
    monkeypatch.setattr(packs, "discover_packs", lambda: [pi])
    result = runner.invoke(app, ["packs"])
    assert result.exit_code == 0, result.output
    assert "po-formulas-x" in result.output
    assert "formulas=flow-a" in result.output
    assert "commands=cmd-b" in result.output


def test_install_missing_uv_prints_pointer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(packs.shutil, "which", lambda _n: None)
    result = runner.invoke(app, ["install", "po-pack"])
    assert result.exit_code == 2
    assert "astral.sh/uv" in result.output
