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
        bd_outputs={"proj-abc": "issue body", "proj-abc-build-iter1": "step spec"},
    )
    text = out.read_text()

    headers = [line for line in text.splitlines() if line.startswith("## ")]
    assert len(headers) == 8
    assert "## Issue" in headers
    assert "## This role-step" in headers
    assert "## Plan" in headers
    assert "## Triage flags" in headers
    assert "## Build diff (latest)" in headers
    assert "## Decision log" in headers
    assert "## Pack-side conventions" in headers
    assert "## Relevant lessons" in headers


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


# ─── backend-aware binary + adopted-id resolution (prefect-orchestration-99k) ──


def _capture_show_ids(
    monkeypatch: pytest.MonkeyPatch, binary: str = "bd"
) -> list[list[str]]:
    """Patch `_resolve_binary` + `subprocess.run`; record every show cmd."""
    import prefect_orchestration.context_bundle as cb

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kw: Any) -> MagicMock:
        calls.append(cmd)
        return _fake_bd_show(stdout="step spec")

    monkeypatch.setattr(cb, "_resolve_binary", lambda _rig: binary)
    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_uses_resolved_backend_binary(
    run_dir: Path, rig_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a br rig the bundle must shell `br show`, not the hardcoded `bd`."""
    calls = _capture_show_ids(monkeypatch, binary="br")
    build_context_md(
        run_dir=run_dir, rig_path=rig_path, issue_id="proj-abc", role="build", iter_n=1
    )
    assert calls, "expected at least one show shellout"
    assert all(c[0] == "br" for c in calls)


def test_resolves_iter_bead_id_from_map(
    run_dir: Path, rig_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the run-dir records the real (br-minted) id, show it — not the
    phantom `<issue>-<role>-iterN` convention id."""
    from prefect_orchestration import iter_bead_ids

    iter_bead_ids.record(run_dir, "proj-abc-build-iter1", "br-real-1")
    calls = _capture_show_ids(monkeypatch)
    build_context_md(
        run_dir=run_dir, rig_path=rig_path, issue_id="proj-abc", role="build", iter_n=1
    )
    shown = [c[2] for c in calls if len(c) > 2 and c[1] == "show"]
    assert "br-real-1" in shown
    assert "proj-abc-build-iter1" not in shown


def test_explicit_iter_bead_id_override_wins(
    run_dir: Path, rig_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit `iter_bead_id` is used verbatim (no map lookup, no compute)."""
    from prefect_orchestration import iter_bead_ids

    iter_bead_ids.record(run_dir, "proj-abc-build-iter1", "br-mapped")
    calls = _capture_show_ids(monkeypatch)
    build_context_md(
        run_dir=run_dir,
        rig_path=rig_path,
        issue_id="proj-abc",
        role="build",
        iter_n=1,
        iter_bead_id="explicit-id",
    )
    shown = [c[2] for c in calls if len(c) > 2 and c[1] == "show"]
    assert "explicit-id" in shown
    assert "br-mapped" not in shown


def test_falls_back_to_convention_id_without_map(
    run_dir: Path, rig_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No map, no override → use the `<issue>-<role>-iterN` convention id
    (correct on dolt where that id is honored)."""
    calls = _capture_show_ids(monkeypatch)
    build_context_md(
        run_dir=run_dir, rig_path=rig_path, issue_id="proj-abc", role="build", iter_n=1
    )
    shown = [c[2] for c in calls if len(c) > 2 and c[1] == "show"]
    assert "proj-abc-build-iter1" in shown


# ─── lessons ledger injection (standards/lessons/*.md) ──────────────────────


def _entry(slug: str, area: str) -> str:
    return (
        f"### {slug} — 2026-06-29 — status: open\n"
        f"- Problem class: {area} miss\n"
        f"- Rule: always {slug}\n"
        f"- Source: soloco-xyz\n"
        f"- Enforcement: needs-enforcement\n"
    )


def _make_ledger(rig_path: Path) -> Path:
    d = rig_path / "standards" / "lessons"
    d.mkdir(parents=True)
    (d / "README.md").write_text("# Lessons ledger\n\nFormat docs, not an entry.\n")
    return d


def test_lessons_none_when_no_dir(
    run_dir: Path, rig_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-SoloCo rig (no standards/lessons/) is a clean no-op."""
    out = _call(run_dir, rig_path, monkeypatch)
    section = out.read_text().split("## Relevant lessons")[1]
    assert "(none)" in section


def test_lessons_none_when_only_readme(
    run_dir: Path, rig_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dir + boilerplate area headers but no real entry → still (none)."""
    d = _make_ledger(rig_path)
    # area file with only the boilerplate header (no `status:` marker)
    (d / "engineering.md").write_text("# Lessons: engineering\n\nAppend-buffer.\n")
    out = _call(run_dir, rig_path, monkeypatch)
    section = out.read_text().split("## Relevant lessons")[1]
    assert "(none)" in section


def test_lessons_injected_when_present(
    run_dir: Path, rig_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = _make_ledger(rig_path)
    (d / "engineering.md").write_text(
        "# Lessons: engineering\n\n" + _entry("run-the-real-thing", "engineering")
    )
    (d / "design.md").write_text(
        "# Lessons: design\n\n" + _entry("no-fake-redesign", "design")
    )
    out = _call(run_dir, rig_path, monkeypatch)
    section = out.read_text().split("## Relevant lessons")[1]
    assert "run-the-real-thing" in section
    assert "no-fake-redesign" in section
    # README content is never injected
    assert "Format docs" not in section


def test_lessons_truncated(
    run_dir: Path, rig_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prefect_orchestration.context_bundle import _lessons_ledger

    d = _make_ledger(rig_path)
    big = "# Lessons: engineering\n\n" + ("status: open\n" + "x" * 100 + "\n") * 500
    (d / "engineering.md").write_text(big)
    out = _lessons_ledger(rig_path, max_chars=2_000)
    assert "truncated" in out
    assert len(out) < 2_200
