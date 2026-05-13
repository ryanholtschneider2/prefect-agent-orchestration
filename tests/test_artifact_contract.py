from __future__ import annotations

import json
from pathlib import Path

from prefect_orchestration.artifact_contract import (
    ARTIFACT_MANIFEST,
    REVIEW_SUMMARY,
    ensure_handoff_summary,
    format_handoff_note,
    write_artifact_manifest,
)


def test_write_artifact_manifest_backend_contract(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    review_dir = run_dir / "review-artifacts"
    review_dir.mkdir(parents=True)
    (review_dir / "summary.md").write_text("# Summary\n")

    manifest_path = write_artifact_manifest(
        run_dir,
        complexity="simple",
        is_docs_only=False,
        has_ui=False,
    )

    payload = json.loads(manifest_path.read_text())
    entries = {entry["artifact_type"]: entry for entry in payload["artifacts"]}

    assert manifest_path == run_dir / ARTIFACT_MANIFEST
    assert payload["work_type"] == "backend-code"
    assert entries["handoff-summary"]["status"] == "present"
    assert entries["build-diff"]["status"] == "missing"
    assert entries["unit-test-log"]["status"] == "skipped"


def test_ensure_handoff_summary_keeps_existing_content(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    summary_path = run_dir / REVIEW_SUMMARY
    summary_path.parent.mkdir(parents=True)
    summary_path.write_text("existing summary\n")

    created_path = ensure_handoff_summary(
        run_dir,
        issue_id="prefect-orchestration-eu8",
        complexity="simple",
        is_docs_only=False,
        has_ui=False,
    )

    assert created_path == summary_path
    assert summary_path.read_text() == "existing summary\n"


def test_format_handoff_note_points_at_contract_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"

    note = format_handoff_note("po complete", run_dir)

    assert "po complete" in note
    assert f"run_dir={run_dir}" in note
    assert f"summary={run_dir / REVIEW_SUMMARY}" in note
    assert f"manifest={run_dir / ARTIFACT_MANIFEST}" in note
