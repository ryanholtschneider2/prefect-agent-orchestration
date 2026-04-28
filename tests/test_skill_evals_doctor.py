"""Unit tests for `prefect_orchestration.skill_evals_doctor.po_skill_evals_fresh`.

Pure stdlib + pytest. We monkeypatch `resolve_pack_skill_dir` to point at
a tmp directory so we don't depend on the real `skills/po/reports/`.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import pytest

from prefect_orchestration import skill_evals_doctor as sed


def _write_report(reports_dir: Path, *, n_passed: int, n_cases: int, finished_at: str) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "latest.json").write_text(
        json.dumps(
            {
                "skill": "po",
                "pack": "prefect-orchestration",
                "n_cases": n_cases,
                "n_passed": n_passed,
                "finished_at": finished_at,
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def fake_skill_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    skill_dir = tmp_path / "skills" / "po"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# po\n")
    monkeypatch.setattr(sed, "resolve_pack_skill_dir", lambda pack, skill: skill_dir)
    return skill_dir


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ago_iso(days: int) -> str:
    dt = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_green_when_fresh_and_passing(fake_skill_dir: Path) -> None:
    _write_report(
        fake_skill_dir / "reports", n_passed=9, n_cases=9, finished_at=_now_iso()
    )
    r = sed.po_skill_evals_fresh()
    assert r.status == "green"
    assert "9/9" in r.message
    assert "100%" in r.message


def test_yellow_when_stale_but_passing(fake_skill_dir: Path) -> None:
    _write_report(
        fake_skill_dir / "reports", n_passed=9, n_cases=9, finished_at=_ago_iso(45)
    )
    r = sed.po_skill_evals_fresh()
    assert r.status == "yellow"
    assert "stale" in r.message
    assert r.hint, "stale check should propose a refresh command"


def test_red_when_below_threshold(fake_skill_dir: Path) -> None:
    _write_report(
        fake_skill_dir / "reports", n_passed=5, n_cases=10, finished_at=_now_iso()
    )
    r = sed.po_skill_evals_fresh()
    assert r.status == "red"
    assert "5/10" in r.message


def test_red_when_report_missing(fake_skill_dir: Path) -> None:
    # No reports/latest.json written.
    r = sed.po_skill_evals_fresh()
    assert r.status == "red"
    assert "missing" in r.message.lower()


def test_red_when_report_malformed(fake_skill_dir: Path) -> None:
    reports = fake_skill_dir / "reports"
    reports.mkdir(parents=True)
    (reports / "latest.json").write_text("{not json", encoding="utf-8")
    r = sed.po_skill_evals_fresh()
    assert r.status == "red"


def test_red_when_finished_at_missing(fake_skill_dir: Path) -> None:
    reports = fake_skill_dir / "reports"
    reports.mkdir(parents=True)
    (reports / "latest.json").write_text(
        json.dumps({"n_cases": 9, "n_passed": 9, "finished_at": ""}),
        encoding="utf-8",
    )
    r = sed.po_skill_evals_fresh()
    assert r.status == "red"


def test_red_when_resolve_pack_skill_dir_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(pack: str, skill: str) -> Path:
        raise RuntimeError("not installed")

    monkeypatch.setattr(sed, "resolve_pack_skill_dir", _boom)
    r = sed.po_skill_evals_fresh()
    assert r.status == "red"
    assert "not installed" in r.message
