"""Unit tests for `context_bundle.build_context_md`."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from prefect_orchestration.context_bundle import build_context_md


def _fake_bd_show(stdout: str = "", returncode: int = 0):
    """Return a subprocess.run result stub."""
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    return result


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "run"
    d.mkdir()
    return d


@pytest.fixture
def rig_path(tmp_path: Path) -> Path:
    return tmp_path / "rig"


def _call(
    run_dir: Path,
    rig_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bd_outputs: dict[str, str] | None = None,
    pack_path: str | None = None,
) -> Path:
    """Call build_context_md with mocked subprocess.run."""
    bd_outputs = bd_outputs or {}

    def fake_run(cmd: list[str], **_kw: Any) -> MagicMock:
        if cmd[0] == "bd" and cmd[1] == "show":
            bead_id = cmd[2]
            output = bd_outputs.get(bead_id, "")
            return _fake_bd_show(stdout=output)
        return _fake_bd_show()

    monkeypatch.setattr(subprocess, "run", fake_run)
    return build_context_md(
        run_dir=run_dir,
        rig_path=rig_path,
        issue_id="proj-abc",
        role="build",
        iter_n=1,
        pack_path=pack_path,
    )


def test_all_present(
    run_dir: Path, rig_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir.joinpath("plan.md").write_text("# Plan")
    run_dir.joinpath("triage.md").write_text("complexity: simple")
    run_dir.joinpath("build-iter-1.diff").write_text("diff content")
    run_dir.joinpath("decision-log.md").write_text("- decision A")

    out = _call(
        run_dir,
        rig_path,
        monkeypatch,
        bd_outputs={"proj-abc": "issue body", "proj-abc.build.iter1": "step spec"},
    )
    text = out.read_text()

    headers = [line for line in text.splitlines() if line.startswith("## ")]
    assert len(headers) == 7
    assert "## Issue" in headers
    assert "## This role-step" in headers
    assert "## Plan" in headers
    assert "## Triage flags" in headers
    assert "## Build diff (latest)" in headers
    assert "## Decision log" in headers
    assert "## Pack-side conventions" in headers


def test_missing_plan(
    run_dir: Path, rig_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _call(run_dir, rig_path, monkeypatch)
    text = out.read_text()
    # Plan section must exist but show (empty)
    assert "## Plan" in text
    plan_section = text.split("## Plan")[1].split("---")[0]
    assert "(empty)" in plan_section


def test_missing_triage(
    run_dir: Path, rig_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _call(run_dir, rig_path, monkeypatch)
    text = out.read_text()
    triage_section = text.split("## Triage flags")[1].split("---")[0]
    assert "(empty)" in triage_section


def test_no_build_diffs(
    run_dir: Path, rig_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _call(run_dir, rig_path, monkeypatch)
    text = out.read_text()
    diff_section = text.split("## Build diff (latest)")[1].split("---")[0]
    assert "(empty)" in diff_section


def test_latest_diff_selected(
    run_dir: Path, rig_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir.joinpath("build-iter-1.diff").write_text("old diff")
    run_dir.joinpath("build-iter-3.diff").write_text("newest diff")

    out = _call(run_dir, rig_path, monkeypatch)
    text = out.read_text()
    diff_section = text.split("## Build diff (latest)")[1].split("---")[0]
    assert "newest diff" in diff_section
    assert "old diff" not in diff_section


def test_no_pack_path(
    run_dir: Path, rig_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _call(run_dir, rig_path, monkeypatch, pack_path=None)
    text = out.read_text()
    conv_section = text.split("## Pack-side conventions")[1]
    assert "(empty)" in conv_section


def test_pack_claude_md_50_lines(
    run_dir: Path, rig_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "CLAUDE.md").write_text("\n".join(f"line {i}" for i in range(100)))

    out = _call(run_dir, rig_path, monkeypatch, pack_path=str(pack))
    text = out.read_text()
    conv_section = text.split("## Pack-side conventions")[1]
    # Exactly 50 lines from the CLAUDE.md
    lines_in_section = [
        ln for ln in conv_section.splitlines() if ln.startswith("line ")
    ]
    assert len(lines_in_section) == 50
    assert "line 0" in lines_in_section
    assert "line 49" in lines_in_section
    assert "line 50" not in lines_in_section


def test_bd_show_failure(
    run_dir: Path, rig_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_run(cmd: list[str], **_kw: Any) -> MagicMock:
        return _fake_bd_show(stdout="", returncode=1)

    monkeypatch.setattr(subprocess, "run", fail_run)

    out = build_context_md(
        run_dir=run_dir,
        rig_path=rig_path,
        issue_id="proj-abc",
        role="build",
        iter_n=1,
    )
    text = out.read_text()
    issue_section = text.split("## Issue")[1].split("---")[0]
    assert "(empty)" in issue_section


def test_idempotent(
    run_dir: Path, rig_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir.joinpath("plan.md").write_text("first plan")
    _call(run_dir, rig_path, monkeypatch)

    run_dir.joinpath("plan.md").write_text("second plan")
    out = _call(run_dir, rig_path, monkeypatch)
    text = out.read_text()
    assert "second plan" in text
    assert "first plan" not in text


def test_no_iter_n(
    run_dir: Path, rig_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(cmd: list[str], **_kw: Any) -> MagicMock:
        return _fake_bd_show(stdout="issue body")

    monkeypatch.setattr(subprocess, "run", fake_run)

    out = build_context_md(
        run_dir=run_dir,
        rig_path=rig_path,
        issue_id="proj-abc",
        role="build",
        iter_n=None,
    )
    text = out.read_text()
    step_section = text.split("## This role-step")[1].split("---")[0]
    assert "(empty)" in step_section
