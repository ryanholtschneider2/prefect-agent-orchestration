from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from subprocess import CompletedProcess


REPO_ROOT = Path(__file__).resolve().parents[3]
PACK_ROOT = REPO_ROOT / "packs" / "po-formulas-retro"
sys.path.insert(0, str(PACK_ROOT))
from po_formulas_retro.flows import (  # noqa: E402
    ImprovementProposal,
    collect_run_dirs,
    extract_codex_log_signals,
    extract_diagnostics_signals,
    dedupe_proposal,
    extract_signals,
    file_follow_up_bead,
    gather_reflection_evidence,
    update_prompts_from_lessons,
)

sys.path.pop(0)


def _write_run(
    root: Path,
    formula: str,
    run_name: str,
    *,
    days_ago: int = 0,
    with_plan: bool = True,
    lessons: str = "",
    decision_log: str = "",
    verdict_payload: dict | None = None,
) -> Path:
    run_dir = root / ".planning" / formula / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    if with_plan:
        (run_dir / "plan.md").write_text("plan")
    if lessons:
        (run_dir / "lessons-learned.md").write_text(lessons)
    if decision_log:
        (run_dir / "decision-log.md").write_text(decision_log)
    if verdict_payload is not None:
        verdict_dir = run_dir / "verdicts"
        verdict_dir.mkdir()
        (verdict_dir / "critic.json").write_text(json.dumps(verdict_payload))
    if days_ago:
        old_time = run_dir.stat().st_mtime - (days_ago * 86400)
        for path in [run_dir, *run_dir.rglob("*")]:
            os.utime(path, (old_time, old_time))
    return run_dir


def test_collect_run_dirs_filters_recent_artifact_runs(tmp_path: Path) -> None:
    kept = _write_run(
        tmp_path, "software-dev-full", "issue-a", lessons="Need a workflow guard."
    )
    _write_run(
        tmp_path,
        "software-dev-full",
        "issue-old",
        lessons="Need a workflow guard.",
        days_ago=20,
    )
    unrelated = tmp_path / ".planning" / "misc" / "empty"
    unrelated.mkdir(parents=True)

    run_dirs = collect_run_dirs(tmp_path, since_days=7)

    assert run_dirs == [kept]


def test_extract_signals_reads_verdicts_and_explicit_lines(tmp_path: Path) -> None:
    run_dir = _write_run(
        tmp_path,
        "software-dev-full",
        "issue-a",
        lessons="- Missing skill for recurring repo setup\n- We should add a workflow guard",
        verdict_payload={
            "verdict": "rejected",
            "checks": [{"name": "tests", "status": "failed"}],
        },
    )

    signals = extract_signals(run_dir)

    assert any(signal.kind == "skill" for signal in signals)
    assert any(
        signal.kind == "workflow" and "critic rejection" in signal.detail.lower()
        for signal in signals
    )
    assert any(
        signal.kind == "hook" and "test failures" in signal.detail.lower()
        for signal in signals
    )


def test_extract_codex_log_signals_mines_pushback_and_artifact_requests(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / ".claude" / "logs"
    log_dir.mkdir(parents=True)
    event_log = log_dir / "session-events.jsonl"
    event_log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "session_id": "sess-1",
                        "hook_event_name": "UserPromptSubmit",
                        "prompt": "This is not good enough. You should just do the next obvious step and make a mermaid diagram.",
                        "captured_at": "2026-05-04T20:00:00Z",
                    }
                ),
                json.dumps(
                    {
                        "session_id": "sess-1",
                        "hook_event_name": "UserPromptSubmit",
                        "prompt": "Please improve the artifact setup and maybe add a skill for this repeated flow.",
                        "captured_at": "2026-05-04T20:01:00Z",
                    }
                ),
            ]
        )
        + "\n"
    )

    signals = extract_codex_log_signals(tmp_path, since_days=7)

    assert any("pushback" in signal.detail.lower() for signal in signals)
    assert any("artifact" in signal.detail.lower() for signal in signals)
    assert any(signal.kind == "skill" for signal in signals)


def test_extract_diagnostics_signals_mines_logfire_failures(tmp_path: Path) -> None:
    log_dir = tmp_path / ".claude" / "logs"
    log_dir.mkdir(parents=True)
    diagnostics_log = log_dir / "diagnostics.jsonl"
    diagnostics_log.write_text(
        json.dumps(
            {
                "timestamp": "2026-05-04T20:00:00Z",
                "message": "OTLP export failed",
                "detail": "http_status=500",
            }
        )
        + "\n"
    )

    signals = extract_diagnostics_signals(tmp_path, since_days=7)

    assert any("logfire export failures" in signal.detail.lower() for signal in signals)


def test_dedupe_skips_existing_repo_capability(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "skills" / "workflow-guard").mkdir(parents=True)
    proposal = ImprovementProposal(
        kind="workflow",
        title="Improve workflow: Add a workflow guard",
        summary="Add a workflow guard",
        evidence=["issue-a:lessons-learned.md"],
        count=2,
        explicit=True,
        search_query="workflow guard",
    )

    monkeypatch.setattr(
        "po_formulas_retro.flows.subprocess.run",
        lambda *args, **kwargs: CompletedProcess(args[0], 0, stdout="[]", stderr=""),
    )

    decision = dedupe_proposal(tmp_path, proposal)

    assert decision.status == "covered"


def test_file_follow_up_bead_uses_silent_bd_create(tmp_path: Path, monkeypatch) -> None:
    proposal = ImprovementProposal(
        kind="skill",
        title="Improve skill: Missing skill for repo setup",
        summary="Missing skill for repo setup",
        evidence=["issue-a:lessons-learned.md"],
        count=2,
        explicit=True,
        search_query="repo setup",
    )
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return CompletedProcess(args, 0, stdout="bd-123\n", stderr="")

    monkeypatch.setattr("po_formulas_retro.flows.subprocess.run", fake_run)

    bead_id = file_follow_up_bead(tmp_path, proposal)

    assert bead_id == "bd-123"
    assert calls[0][:3] == ["bd", "create", "--title"]
    assert "--silent" in calls[0]


def test_flow_writes_report_and_files_new_beads(tmp_path: Path, monkeypatch) -> None:
    _write_run(
        tmp_path,
        "software-dev-full",
        "issue-a",
        lessons="- Missing skill for recurring repo setup",
    )
    _write_run(
        tmp_path,
        "software-dev-edit",
        "issue-b",
        lessons="- Missing skill for recurring repo setup",
    )

    def fake_run(args, **kwargs):
        if args[:2] == ["bd", "search"]:
            return CompletedProcess(args, 0, stdout="[]", stderr="")
        if args[:2] == ["bd", "create"]:
            return CompletedProcess(args, 0, stdout="bd-555\n", stderr="")
        raise AssertionError(args)

    monkeypatch.setattr("po_formulas_retro.flows.subprocess.run", fake_run)

    result = update_prompts_from_lessons.fn(
        rig_path=str(tmp_path),
        lookback_days=7,
        auto_file_beads=True,
        report_slug="weekly-check",
    )

    report_dir = tmp_path / ".planning" / "update-prompts-from-lessons" / "weekly-check"
    report_json = json.loads((report_dir / "report.json").read_text())
    report_md = (report_dir / "report.md").read_text()

    assert result["status"] == "ok"
    assert result["filed_beads"] == ["bd-555"]
    assert report_dir.exists()
    assert "bd-555" in report_md
    assert report_json["proposals"][0]["dedupe_status"] == "new"


def test_gather_reflection_evidence_includes_codex_logs(tmp_path: Path) -> None:
    _write_run(
        tmp_path,
        "software-dev-full",
        "issue-a",
        lessons="- Missing skill for recurring repo setup",
    )
    log_dir = tmp_path / ".claude" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "session-events.jsonl").write_text(
        json.dumps(
            {
                "session_id": "sess-1",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "This is not good enough; add a diagram.",
                "captured_at": "2026-05-04T20:00:00Z",
            }
        )
        + "\n"
    )

    evidence = gather_reflection_evidence(tmp_path, since_days=7)

    assert any(item.source == ".claude/logs/session-events.jsonl" for item in evidence)
