"""Unit tests for `po resume` (prefect-orchestration-1zq).

`po resume` differs from `po retry` in two ways:

1. Run-dir is NOT archived — bd-metadata verdicts on iter beads persist.
2. The flow runs with `PO_RESUME=1` in env, which makes
   `prompt_for_bead_verdict` short-circuit on already-stamped beads.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from prefect_orchestration import resume, run_lookup


# ── _list_completed_steps ─────────────────────────────────────────


def test_list_completed_steps_no_issue_id_uses_legacy_dir(tmp_path: Path) -> None:
    """Without issue_id, the legacy file-based fallback still works for old run dirs."""
    assert resume._list_completed_steps(tmp_path) == []


def test_list_completed_steps_legacy_dir_returns_stems_sorted(tmp_path: Path) -> None:
    vdir = tmp_path / "verdicts"
    vdir.mkdir()
    (vdir / "triage.json").write_text("{}")
    (vdir / "plan-iter-1.json").write_text("{}")
    (vdir / "review-iter-2.json").write_text("{}")

    assert resume._list_completed_steps(tmp_path) == [
        "plan-iter-1",
        "review-iter-2",
        "triage",
    ]


def test_list_completed_steps_with_issue_id_walks_bd(
    tmp_path: Path, monkeypatch
) -> None:
    """With issue_id supplied, _list_completed_steps queries bd for iter beads."""
    fake_rows = json.dumps(
        [
            {
                "id": "iss-1-triage-iter1",
                "metadata": {"po.triage": {"complexity": "moderate"}},
            },
            {
                "id": "iss-1-plan-iter1",
                "metadata": {"po.plan": {"verdict": "approved"}},
            },
            {
                "id": "iss-1-plan-iter2",
                "metadata": {"po.run_dir": "/tmp/x"},  # bookkeeping only — no verdict
            },
        ]
    )

    class _FakeProc:
        returncode = 0
        stdout = fake_rows

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _FakeProc())
    out = resume._list_completed_steps(tmp_path, issue_id="iss-1")
    assert out == ["plan-iter-1", "triage-iter-1"]


# ── resume_issue end-to-end (with mocks) ──────────────────────────


def _stub_run_loc(rig_path: Path, run_dir: Path) -> mock.Mock:
    loc = mock.Mock()
    loc.rig_path = rig_path
    loc.run_dir = run_dir
    return loc


def test_resume_does_not_archive_run_dir(tmp_path: Path, monkeypatch) -> None:
    rig = tmp_path / "rig"
    rig.mkdir()
    run_dir = rig / ".planning" / "software-dev-full" / "iss-1"
    run_dir.mkdir(parents=True)
    (run_dir / "triage.md").write_text("existing content")

    monkeypatch.setattr(
        run_lookup, "resolve_run_dir", lambda iid: _stub_run_loc(rig, run_dir)
    )
    monkeypatch.setattr(resume, "_load_formula", lambda name: lambda **kw: "ok")
    monkeypatch.setattr(resume, "_bd_show_status", lambda iid: "open")

    # Stub bd list to return no iter beads.
    class _Empty:
        returncode = 0
        stdout = "[]"

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _Empty())

    res = resume.resume_issue("iss-1", force=True)

    # Run-dir + content untouched.
    assert (run_dir / "triage.md").read_text() == "existing content"
    # No `.bak-` siblings created.
    siblings = list(run_dir.parent.glob("iss-1.bak-*"))
    assert siblings == []
    assert res.flow_result == "ok"


def test_resume_sets_po_resume_env_during_flow_call(
    tmp_path: Path, monkeypatch
) -> None:
    rig = tmp_path / "rig"
    rig.mkdir()
    run_dir = rig / ".planning" / "software-dev-full" / "iss-2"
    run_dir.mkdir(parents=True)

    monkeypatch.setattr(
        run_lookup, "resolve_run_dir", lambda iid: _stub_run_loc(rig, run_dir)
    )
    monkeypatch.setattr(resume, "_bd_show_status", lambda iid: "open")
    monkeypatch.delenv("PO_RESUME", raising=False)

    class _Empty:
        returncode = 0
        stdout = "[]"

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _Empty())

    captured: dict[str, str | None] = {}

    def fake_flow(**kwargs) -> str:
        captured["po_resume"] = os.environ.get("PO_RESUME")
        return "ok"

    monkeypatch.setattr(resume, "_load_formula", lambda name: fake_flow)

    resume.resume_issue("iss-2", force=True)

    assert captured["po_resume"] == "1"
    assert os.environ.get("PO_RESUME") is None


def test_resume_restores_prior_po_resume_env(tmp_path: Path, monkeypatch) -> None:
    rig = tmp_path / "rig"
    rig.mkdir()
    run_dir = rig / ".planning" / "software-dev-full" / "iss-3"
    run_dir.mkdir(parents=True)

    monkeypatch.setattr(
        run_lookup, "resolve_run_dir", lambda iid: _stub_run_loc(rig, run_dir)
    )
    monkeypatch.setattr(resume, "_bd_show_status", lambda iid: "open")
    monkeypatch.setattr(resume, "_load_formula", lambda name: lambda **kw: "ok")
    monkeypatch.setenv("PO_RESUME", "prior-value")

    class _Empty:
        returncode = 0
        stdout = "[]"

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _Empty())

    resume.resume_issue("iss-3", force=True)

    assert os.environ.get("PO_RESUME") == "prior-value"


def test_resume_refuses_when_run_dir_missing(tmp_path: Path, monkeypatch) -> None:
    rig = tmp_path / "rig"
    rig.mkdir()
    nonexistent = rig / ".planning" / "software-dev-full" / "iss-x"

    monkeypatch.setattr(
        run_lookup, "resolve_run_dir", lambda iid: _stub_run_loc(rig, nonexistent)
    )

    with pytest.raises(resume.ResumeError) as exc_info:
        resume.resume_issue("iss-x", force=True)
    assert exc_info.value.exit_code == 6
    assert "does not exist" in str(exc_info.value)


def test_resume_reopens_closed_bead(tmp_path: Path, monkeypatch) -> None:
    rig = tmp_path / "rig"
    rig.mkdir()
    run_dir = rig / ".planning" / "software-dev-full" / "iss-4"
    run_dir.mkdir(parents=True)

    monkeypatch.setattr(
        run_lookup, "resolve_run_dir", lambda iid: _stub_run_loc(rig, run_dir)
    )
    monkeypatch.setattr(resume, "_load_formula", lambda name: lambda **kw: "ok")
    monkeypatch.setattr(resume, "_bd_show_status", lambda iid: "closed")
    reopen_calls: list[str] = []
    monkeypatch.setattr(resume, "_bd_reopen", lambda iid: reopen_calls.append(iid))

    class _Empty:
        returncode = 0
        stdout = "[]"

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _Empty())

    res = resume.resume_issue("iss-4", force=True)

    assert reopen_calls == ["iss-4"]
    assert res.reopened is True


def test_resume_refuses_when_in_flight(tmp_path: Path, monkeypatch) -> None:
    rig = tmp_path / "rig"
    rig.mkdir()
    run_dir = rig / ".planning" / "software-dev-full" / "iss-5"
    run_dir.mkdir(parents=True)

    monkeypatch.setattr(
        run_lookup, "resolve_run_dir", lambda iid: _stub_run_loc(rig, run_dir)
    )

    with pytest.raises(resume.ResumeError) as exc_info:
        resume.resume_issue("iss-5", _in_flight_probe=lambda iid: 1)
    assert exc_info.value.exit_code == 3
    assert "Running" in str(exc_info.value)


# ── resume --at (scheduled path) ──────────────────────────────────────


def test_resume_with_at_schedules(tmp_path: Path, monkeypatch) -> None:
    from datetime import datetime, timezone

    rig = tmp_path / "rig"
    rig.mkdir()
    run_dir = rig / ".planning" / "software-dev-full" / "iss-6"
    run_dir.mkdir(parents=True)

    monkeypatch.setattr(
        run_lookup, "resolve_run_dir", lambda iid: _stub_run_loc(rig, run_dir)
    )
    monkeypatch.setattr(resume, "_bd_show_status", lambda iid: "open")

    scheduled_time = datetime(2099, 1, 1, tzinfo=timezone.utc)

    class _FakeFlowRun:
        id = "fr-scheduled"

    async def fake_schedule(formula_name, rig_name, rp, iid, when):
        return _FakeFlowRun(), "myflow/myflow-manual", scheduled_time

    monkeypatch.setattr(resume, "_schedule_resume", fake_schedule)

    res = resume.resume_issue("iss-6", force=True, when="2h")

    assert "scheduled" in res.flow_result
    assert "fr-scheduled" in res.flow_result
    assert run_dir.exists()


def test_resume_with_at_scheduling_fails(tmp_path: Path, monkeypatch) -> None:
    rig = tmp_path / "rig"
    rig.mkdir()
    run_dir = rig / ".planning" / "software-dev-full" / "iss-7"
    run_dir.mkdir(parents=True)

    monkeypatch.setattr(
        run_lookup, "resolve_run_dir", lambda iid: _stub_run_loc(rig, run_dir)
    )
    monkeypatch.setattr(resume, "_bd_show_status", lambda iid: "open")

    async def failing_schedule(*args):
        raise RuntimeError("Prefect server unreachable")

    monkeypatch.setattr(resume, "_schedule_resume", failing_schedule)

    with pytest.raises(resume.ResumeError) as exc_info:
        resume.resume_issue("iss-7", force=True, when="2h")
    assert exc_info.value.exit_code == 5
    assert "failed to schedule resume" in str(exc_info.value)
