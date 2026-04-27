"""Unit tests for `po resume` (prefect-orchestration-1zq).

`po resume` differs from `po retry` in two ways:

1. Run-dir is NOT archived — verdicts already on disk persist.
2. The flow runs with `PO_RESUME=1` in env, which makes
   `prompt_for_verdict` short-circuit on existing verdict files.

These tests pin both contracts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from prefect_orchestration import parsing, resume, run_lookup


# ── parsing.prompt_for_verdict short-circuit ────────────────────────


def test_prompt_for_verdict_short_circuits_on_resume(tmp_path: Path, monkeypatch) -> None:
    """When PO_RESUME=1 and the verdict file exists, the agent is NOT prompted."""
    vdir = tmp_path / "verdicts"
    vdir.mkdir()
    (vdir / "triage.json").write_text(json.dumps({"has_ui": True, "is_docs_only": False}))

    sess = mock.Mock()
    monkeypatch.setenv("PO_RESUME", "1")

    out = parsing.prompt_for_verdict(sess, "do triage", tmp_path, "triage")

    assert out == {"has_ui": True, "is_docs_only": False}
    sess.prompt.assert_not_called()


def test_prompt_for_verdict_runs_normally_without_resume_env(tmp_path: Path, monkeypatch) -> None:
    """Absent PO_RESUME, the agent is prompted even if a verdict pre-exists."""
    vdir = tmp_path / "verdicts"
    vdir.mkdir()
    (vdir / "triage.json").write_text(json.dumps({"has_ui": False}))

    sess = mock.Mock()
    monkeypatch.delenv("PO_RESUME", raising=False)

    parsing.prompt_for_verdict(sess, "do triage", tmp_path, "triage")
    sess.prompt.assert_called_once()


def test_prompt_for_verdict_resume_falls_through_when_verdict_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """PO_RESUME=1 + no verdict on disk → still prompts the agent."""
    sess = mock.Mock()
    monkeypatch.setenv("PO_RESUME", "1")

    # Agent's prompt() side-effect: write the verdict file.
    def _write_verdict(*args, **kwargs):
        vdir = tmp_path / "verdicts"
        vdir.mkdir(exist_ok=True)
        (vdir / "triage.json").write_text(json.dumps({"has_ui": True}))

    sess.prompt.side_effect = _write_verdict

    out = parsing.prompt_for_verdict(sess, "do triage", tmp_path, "triage")
    sess.prompt.assert_called_once()
    assert out == {"has_ui": True}


# ── _list_completed_steps ─────────────────────────────────────────


def test_list_completed_steps_empty_when_no_verdicts(tmp_path: Path) -> None:
    assert resume._list_completed_steps(tmp_path) == []


def test_list_completed_steps_returns_stems_sorted(tmp_path: Path) -> None:
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


def test_list_completed_steps_ignores_non_json(tmp_path: Path) -> None:
    vdir = tmp_path / "verdicts"
    vdir.mkdir()
    (vdir / "triage.json").write_text("{}")
    (vdir / "scratch.txt").write_text("not a verdict")
    assert resume._list_completed_steps(tmp_path) == ["triage"]


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
    (run_dir / "verdicts").mkdir()
    (run_dir / "verdicts" / "triage.json").write_text("{}")

    monkeypatch.setattr(
        run_lookup, "resolve_run_dir", lambda iid: _stub_run_loc(rig, run_dir)
    )
    monkeypatch.setattr(resume, "_load_formula", lambda name: lambda **kw: "ok")
    monkeypatch.setattr(resume, "_bd_show_status", lambda iid: "open")

    res = resume.resume_issue("iss-1", force=True)

    # Run-dir + content untouched.
    assert (run_dir / "triage.md").read_text() == "existing content"
    assert (run_dir / "verdicts" / "triage.json").exists()
    # No `.bak-` siblings created.
    siblings = list(run_dir.parent.glob("iss-1.bak-*"))
    assert siblings == []
    # Result reports the steps already complete.
    assert res.completed_steps == ["triage"]
    assert res.flow_result == "ok"


def test_resume_sets_po_resume_env_during_flow_call(tmp_path: Path, monkeypatch) -> None:
    rig = tmp_path / "rig"
    rig.mkdir()
    run_dir = rig / ".planning" / "software-dev-full" / "iss-2"
    run_dir.mkdir(parents=True)

    monkeypatch.setattr(
        run_lookup, "resolve_run_dir", lambda iid: _stub_run_loc(rig, run_dir)
    )
    monkeypatch.setattr(resume, "_bd_show_status", lambda iid: "open")
    monkeypatch.delenv("PO_RESUME", raising=False)

    captured: dict[str, str | None] = {}

    def fake_flow(**kwargs) -> str:
        captured["po_resume"] = os.environ.get("PO_RESUME")
        return "ok"

    monkeypatch.setattr(resume, "_load_formula", lambda name: fake_flow)

    resume.resume_issue("iss-2", force=True)

    assert captured["po_resume"] == "1"
    # Env restored after the flow returns (not leaking to subsequent calls).
    assert os.environ.get("PO_RESUME") is None


def test_resume_restores_prior_po_resume_env(tmp_path: Path, monkeypatch) -> None:
    """If PO_RESUME was already set (nested resume), restore the prior value."""
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

    resume.resume_issue("iss-3", force=True)

    assert os.environ.get("PO_RESUME") == "prior-value"


def test_resume_refuses_when_run_dir_missing(tmp_path: Path, monkeypatch) -> None:
    """Cannot resume a flow that was never started (or was retried-archived)."""
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
