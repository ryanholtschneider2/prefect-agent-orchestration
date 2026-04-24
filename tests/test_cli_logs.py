"""Unit tests for `po logs` Typer subcommand."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prefect_orchestration import cli, run_lookup


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _seed_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    older = run_dir / "lint-iter-1.log"
    newer = run_dir / "decision-log.md"
    older.write_text("old log line\n" * 5)
    newer.write_text("\n".join(f"line-{i}" for i in range(10)) + "\n")
    os.utime(older, (1000, 1000))
    os.utime(newer, (9999, 9999))
    return run_dir


def test_logs_prints_tail_of_freshest(tmp_path, runner, monkeypatch):
    run_dir = _seed_run_dir(tmp_path)
    loc = run_lookup.RunLocation(rig_path=tmp_path, run_dir=run_dir)
    monkeypatch.setattr(run_lookup, "PREFECT_LOG_DIR", tmp_path / "no-prefect")
    monkeypatch.setattr(cli._run_lookup, "resolve_run_dir", lambda _id: loc)
    result = runner.invoke(cli.app, ["logs", "beads-xyz", "-n", "3"])
    assert result.exit_code == 0, result.stderr
    assert "===== decision-log.md =====" in result.stdout
    assert "line-9" in result.stdout
    # Only last 3 lines requested
    assert "line-6" not in result.stdout


def test_logs_missing_metadata_shows_fix_hint(tmp_path, runner, monkeypatch):
    def raiser(_id: str):
        raise run_lookup.RunDirNotFound(
            "no run_dir recorded for beads-xyz. "
            "bd update beads-xyz --set-metadata po.rig_path=<abs> --set-metadata po.run_dir=<abs>"
        )
    monkeypatch.setattr(cli._run_lookup, "resolve_run_dir", raiser)
    result = runner.invoke(cli.app, ["logs", "beads-xyz"])
    assert result.exit_code == 2
    assert "bd update beads-xyz" in result.stderr
    assert "--set-metadata po.rig_path=" in result.stderr


def test_logs_follow_execs_tail(tmp_path, runner, monkeypatch):
    run_dir = _seed_run_dir(tmp_path)
    loc = run_lookup.RunLocation(rig_path=tmp_path, run_dir=run_dir)
    monkeypatch.setattr(run_lookup, "PREFECT_LOG_DIR", tmp_path / "no-prefect")
    monkeypatch.setattr(cli._run_lookup, "resolve_run_dir", lambda _id: loc)

    captured: dict = {}

    def fake_execvp(prog, argv):
        captured["prog"] = prog
        captured["argv"] = argv
        # Raise to unwind instead of actually exec-ing.
        raise SystemExit(0)

    monkeypatch.setattr(cli.os, "execvp", fake_execvp)
    result = runner.invoke(cli.app, ["logs", "beads-xyz", "-f", "-n", "50"])
    assert result.exit_code == 0
    assert captured["prog"] == "tail"
    assert captured["argv"][:4] == ["tail", "-n", "50", "-F"]
    assert captured["argv"][4].endswith("decision-log.md")


def test_logs_file_override(tmp_path, runner, monkeypatch):
    run_dir = _seed_run_dir(tmp_path)
    loc = run_lookup.RunLocation(rig_path=tmp_path, run_dir=run_dir)
    monkeypatch.setattr(cli._run_lookup, "resolve_run_dir", lambda _id: loc)
    result = runner.invoke(
        cli.app, ["logs", "beads-xyz", "--file", "lint-iter-1.log", "-n", "100"]
    )
    assert result.exit_code == 0
    assert "lint-iter-1.log" in result.stdout
    assert "old log line" in result.stdout


def test_logs_file_override_missing(tmp_path, runner, monkeypatch):
    run_dir = _seed_run_dir(tmp_path)
    loc = run_lookup.RunLocation(rig_path=tmp_path, run_dir=run_dir)
    monkeypatch.setattr(cli._run_lookup, "resolve_run_dir", lambda _id: loc)
    result = runner.invoke(cli.app, ["logs", "beads-xyz", "--file", "nope.log"])
    assert result.exit_code == 3
    assert "no such file" in result.stderr
