"""Unit tests for `po sessions` Typer subcommand + sessions helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prefect_orchestration import cli, run_lookup, sessions


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _seed_run_dir(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    metadata = {
        "session_triager": "uuid-triager-1",
        "session_builder": "uuid-builder-2",
        "session_critic": "uuid-critic-3",
        "has_ui": "false",
    }
    (run_dir / "metadata.json").write_text(json.dumps(metadata))

    (run_dir / "triage.md").write_text("t\n")
    (run_dir / "build-iter-1.diff").write_text("diff1\n")
    (run_dir / "build-iter-2.diff").write_text("diff2\n")
    (run_dir / "critique-iter-1.md").write_text("c1\n")

    # Deterministic mtimes
    os.utime(run_dir / "metadata.json", (1_700_000_000, 1_700_000_000))
    os.utime(run_dir / "triage.md", (1_700_000_100, 1_700_000_100))
    os.utime(run_dir / "build-iter-1.diff", (1_700_000_200, 1_700_000_200))
    os.utime(run_dir / "build-iter-2.diff", (1_700_000_300, 1_700_000_300))
    os.utime(run_dir / "critique-iter-1.md", (1_700_000_150, 1_700_000_150))
    return run_dir, metadata


def test_sessions_prints_table(tmp_path, runner, monkeypatch):
    run_dir, _ = _seed_run_dir(tmp_path)
    loc = run_lookup.RunLocation(rig_path=tmp_path, run_dir=run_dir)
    monkeypatch.setattr(cli._run_lookup, "resolve_run_dir", lambda _id: loc)

    result = runner.invoke(cli.app, ["sessions", "beads-xyz"])
    assert result.exit_code == 0, result.stderr
    out = result.stdout
    for header in ("ROLE", "UUID", "LAST-ITER", "LAST-UPDATED"):
        assert header in out
    assert "triager" in out
    assert "uuid-triager-1" in out
    assert "builder" in out
    assert "uuid-builder-2" in out
    assert "critic" in out
    assert "uuid-critic-3" in out
    # Max iter for builder = 2
    builder_line = next(line for line in out.splitlines() if "builder" in line)
    assert (
        " 2 " in builder_line
        or builder_line.rstrip().endswith("2")
        or "2  " in builder_line
    )
    # Triager has no iter artifact with -iter-N, so should be "-"
    triager_line = next(line for line in out.splitlines() if "triager" in line)
    assert " - " in triager_line or triager_line.split()[2] == "-"


def test_sessions_resume_emits_one_liner(tmp_path, runner, monkeypatch):
    run_dir, _ = _seed_run_dir(tmp_path)
    loc = run_lookup.RunLocation(rig_path=tmp_path, run_dir=run_dir)
    monkeypatch.setattr(cli._run_lookup, "resolve_run_dir", lambda _id: loc)

    result = runner.invoke(cli.app, ["sessions", "beads-xyz", "--resume", "builder"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == (
        "claude --print --resume uuid-builder-2 --fork-session"
    )


def test_sessions_resume_unknown_role(tmp_path, runner, monkeypatch):
    run_dir, _ = _seed_run_dir(tmp_path)
    loc = run_lookup.RunLocation(rig_path=tmp_path, run_dir=run_dir)
    monkeypatch.setattr(cli._run_lookup, "resolve_run_dir", lambda _id: loc)

    result = runner.invoke(cli.app, ["sessions", "beads-xyz", "--resume", "nonesuch"])
    assert result.exit_code == 4
    assert "no session recorded for role" in result.stderr
    assert "'nonesuch'" in result.stderr


def test_sessions_missing_metadata_json(tmp_path, runner, monkeypatch):
    # run_dir exists but metadata.json is absent.
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    loc = run_lookup.RunLocation(rig_path=tmp_path, run_dir=run_dir)
    monkeypatch.setattr(cli._run_lookup, "resolve_run_dir", lambda _id: loc)

    result = runner.invoke(cli.app, ["sessions", "beads-xyz"])
    assert result.exit_code == 3
    assert "metadata.json" in result.stderr


def test_sessions_run_dir_not_found(tmp_path, runner, monkeypatch):
    def raiser(_id: str):
        raise run_lookup.RunDirNotFound(
            "no run_dir recorded for beads-xyz. "
            "bd update beads-xyz --set-metadata po.rig_path=<abs> --set-metadata po.run_dir=<abs>"
        )

    monkeypatch.setattr(cli._run_lookup, "resolve_run_dir", raiser)
    result = runner.invoke(cli.app, ["sessions", "beads-xyz"])
    assert result.exit_code == 2
    assert "bd update beads-xyz" in result.stderr


def test_build_rows_sorted_and_unknown_role_shows_dashes(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    metadata = {
        "session_zeta": "uuid-z",  # unknown role — not in ROLE_ARTIFACT_GLOBS
        "session_builder": "uuid-b",
        "not_a_session": "ignored",
    }
    (run_dir / "metadata.json").write_text(json.dumps(metadata))
    os.utime(run_dir / "metadata.json", (1_700_000_000, 1_700_000_000))

    rows = sessions.build_rows(run_dir, metadata)
    assert [r.role for r in rows] == ["builder", "zeta"]
    # Unknown role with no artifacts: "-" iter, meta.json mtime for last-updated
    zeta = rows[1]
    assert zeta.last_iter == "-"
    assert zeta.last_updated != "-"
    # builder has no build-iter artifacts here either → "-"
    assert rows[0].last_iter == "-"


def test_render_table_header_only_when_empty():
    out = sessions.render_table([])
    lines = out.splitlines()
    assert lines[0].split()[0] == "ROLE"
    assert set(lines[1].replace(" ", "")) == {"-"}
    assert len(lines) == 2


def test_render_table_pod_column_hidden_when_no_k8s():
    rows = [sessions.SessionRow("builder", "u1", "1", "now", pod=None)]
    out = sessions.render_table(rows)
    assert "POD" not in out


def test_render_table_pod_column_shown_when_any_row_has_k8s():
    rows = [sessions.SessionRow("builder", "u1", "1", "now", pod="po-worker-7c5")]
    out = sessions.render_table(rows)
    assert "POD" in out
    assert "po-worker-7c5" in out


def test_build_rows_passes_pod_through(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "metadata.json").write_text(json.dumps({"session_builder": "u"}))
    rows = sessions.build_rows(run_dir, {"session_builder": "u"}, pod="pod-7")
    assert rows[0].pod == "pod-7"


def test_sessions_cli_shows_pod_when_bead_metadata_set(tmp_path, runner, monkeypatch):
    run_dir, _ = _seed_run_dir(tmp_path)
    loc = run_lookup.RunLocation(rig_path=tmp_path, run_dir=run_dir)
    monkeypatch.setattr(cli._run_lookup, "resolve_run_dir", lambda _id: loc)
    monkeypatch.setattr(
        cli._attach,
        "fetch_bead_metadata",
        lambda _id: {"po.k8s_pod": "po-worker-xyz"},
    )

    result = runner.invoke(cli.app, ["sessions", "beads-xyz"])
    assert result.exit_code == 0, result.stderr
    assert "POD" in result.stdout
    assert "po-worker-xyz" in result.stdout
