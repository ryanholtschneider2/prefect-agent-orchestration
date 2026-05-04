"""Unit tests for `po artifacts` Typer subcommand."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prefect_orchestration import cli, run_lookup


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _seed_full_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "triage.md").write_text("# triage body\n")
    (run_dir / "plan.md").write_text("# plan body\n")
    (run_dir / "critique-iter-1.md").write_text("critique 1\n")
    (run_dir / "verification-report-iter-1.md").write_text("verify 1\n")
    (run_dir / "critique-iter-2.md").write_text("critique 2\n")
    (run_dir / "verification-report-iter-2.md").write_text("verify 2\n")
    (run_dir / "critique-iter-10.md").write_text("critique 10\n")
    (run_dir / "verification-report-iter-10.md").write_text("verify 10\n")
    review_dir = run_dir / "review-artifacts"
    review_dir.mkdir()
    (review_dir / "summary.md").write_text("# summary\n")
    (run_dir / "artifact-manifest.json").write_text(
        json.dumps({"contract_version": 1, "artifacts": []})
    )
    (run_dir / "decision-log.md").write_text("decisions\n")
    (run_dir / "lessons-learned.md").write_text("lessons\n")
    verdicts = run_dir / "verdicts"
    verdicts.mkdir()
    (verdicts / "triage.json").write_text(json.dumps({"verdict": "pass"}))
    (verdicts / "build-iter-1.json").write_text(json.dumps({"verdict": "pass"}))
    return run_dir


def _patch_resolve(monkeypatch, run_dir: Path, tmp_path: Path) -> None:
    loc = run_lookup.RunLocation(rig_path=tmp_path, run_dir=run_dir)
    monkeypatch.setattr(cli._run_lookup, "resolve_run_dir", lambda _id: loc)


def test_artifacts_full_order(tmp_path, runner, monkeypatch):
    run_dir = _seed_full_run(tmp_path)
    _patch_resolve(monkeypatch, run_dir, tmp_path)

    result = runner.invoke(cli.app, ["artifacts", "beads-xyz"])
    assert result.exit_code == 0, result.stderr
    out = result.stdout

    order = [
        "triage.md",
        "plan.md",
        "critique-iter-1.md",
        "verification-report-iter-1.md",
        "critique-iter-2.md",
        "verification-report-iter-2.md",
        "critique-iter-10.md",
        "verification-report-iter-10.md",
        "review-artifacts/summary.md",
        "artifact-manifest.json",
        "decision-log.md",
        "lessons-learned.md",
        "verdicts/build-iter-1.json",
        "verdicts/triage.json",
    ]
    positions = [out.find(f"===== {name} =====") for name in order]
    assert all(p >= 0 for p in positions), positions
    assert positions == sorted(positions), positions


def test_artifacts_missing_files_render_missing(tmp_path, runner, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "triage.md").write_text("just triage\n")
    _patch_resolve(monkeypatch, run_dir, tmp_path)

    result = runner.invoke(cli.app, ["artifacts", "beads-xyz"])
    assert result.exit_code == 0
    assert "===== triage.md =====" in result.stdout
    assert "===== plan.md =====" in result.stdout
    assert "(missing)" in result.stdout


def test_artifacts_verdicts_only(tmp_path, runner, monkeypatch):
    run_dir = _seed_full_run(tmp_path)
    _patch_resolve(monkeypatch, run_dir, tmp_path)

    result = runner.invoke(cli.app, ["artifacts", "beads-xyz", "--verdicts"])
    assert result.exit_code == 0
    out = result.stdout
    assert "===== verdicts/triage.json =====" in out
    assert "===== verdicts/build-iter-1.json =====" in out
    assert "===== triage.md =====" not in out
    assert "===== plan.md =====" not in out
    # Pretty-printed JSON
    assert '"verdict": "pass"' in out


def test_artifacts_open_uses_editor(tmp_path, runner, monkeypatch):
    run_dir = _seed_full_run(tmp_path)
    _patch_resolve(monkeypatch, run_dir, tmp_path)
    monkeypatch.setenv("EDITOR", "my-editor")

    captured: dict = {}

    def fake_run(argv, check=False):
        captured["argv"] = argv
        captured["check"] = check

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = runner.invoke(cli.app, ["artifacts", "beads-xyz", "--open"])
    assert result.exit_code == 0, result.stderr
    assert captured["argv"] == ["my-editor", str(run_dir)]


def test_artifacts_open_fallback_to_xdg_open(tmp_path, runner, monkeypatch):
    run_dir = _seed_full_run(tmp_path)
    _patch_resolve(monkeypatch, run_dir, tmp_path)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/bin/xdg-open")

    captured: dict = {}

    def fake_run(argv, check=False):
        captured["argv"] = argv

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = runner.invoke(cli.app, ["artifacts", "beads-xyz", "--open"])
    assert result.exit_code == 0, result.stderr
    assert captured["argv"] == ["/usr/bin/xdg-open", str(run_dir)]


def test_artifacts_open_no_editor_no_xdg(tmp_path, runner, monkeypatch):
    run_dir = _seed_full_run(tmp_path)
    _patch_resolve(monkeypatch, run_dir, tmp_path)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)

    result = runner.invoke(cli.app, ["artifacts", "beads-xyz", "--open"])
    assert result.exit_code == 5
    assert "xdg-open" in result.stderr


def test_artifacts_clean_output_no_ansi(tmp_path, runner, monkeypatch):
    run_dir = _seed_full_run(tmp_path)
    _patch_resolve(monkeypatch, run_dir, tmp_path)

    result = runner.invoke(cli.app, ["artifacts", "beads-xyz"])
    assert result.exit_code == 0
    assert "\x1b[" not in result.stdout


def test_artifacts_missing_metadata_shows_fix_hint(tmp_path, runner, monkeypatch):
    def raiser(_id: str):
        raise run_lookup.RunDirNotFound(
            "no run_dir recorded for beads-xyz. "
            "bd update beads-xyz --set-metadata po.rig_path=<abs> --set-metadata po.run_dir=<abs>"
        )

    monkeypatch.setattr(cli._run_lookup, "resolve_run_dir", raiser)
    result = runner.invoke(cli.app, ["artifacts", "beads-xyz"])
    assert result.exit_code == 2
    assert "bd update beads-xyz" in result.stderr
