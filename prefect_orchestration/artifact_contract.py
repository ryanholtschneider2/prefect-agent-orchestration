"""Shared artifact contract for PO run directories."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

ARTIFACT_MANIFEST = "artifact-manifest.json"
REVIEW_ARTIFACTS_DIR = "review-artifacts"
REVIEW_SUMMARY = f"{REVIEW_ARTIFACTS_DIR}/summary.md"
VERDICTS_DIR = "verdicts"
TRANSCRIPTS_DIR = "transcripts"

WorkType = Literal["backend-code", "ui", "docs-only", "verification-heavy"]


def classify_work_type(
    *,
    complexity: str,
    is_docs_only: bool,
    has_ui: bool,
) -> WorkType:
    if is_docs_only:
        return "docs-only"
    if has_ui:
        return "ui"
    if complexity == "complex":
        return "verification-heavy"
    return "backend-code"


def contract_paths(run_dir: Path) -> dict[str, Path]:
    return {
        "run_dir": run_dir,
        "manifest": run_dir / ARTIFACT_MANIFEST,
        "summary": run_dir / REVIEW_SUMMARY,
    }


def format_handoff_note(prefix: str, run_dir: Path) -> str:
    paths = contract_paths(run_dir)
    return (
        f"{prefix} | run_dir={paths['run_dir']} | summary={paths['summary']} "
        f"| manifest={paths['manifest']}"
    )


def _artifact_specs(work_type: WorkType) -> list[dict[str, object]]:
    base = [
        {
            "artifact_type": "handoff-summary",
            "path": REVIEW_SUMMARY,
            "producer_role": "review-artifacts",
            "required": True,
        },
        {
            "artifact_type": "decision-log",
            "path": "decision-log.md",
            "producer_role": "build",
            "required": False,
        },
    ]
    if work_type == "docs-only":
        return base + [
            {
                "artifact_type": "plan",
                "path": "plan.md",
                "producer_role": "plan",
                "required": False,
            },
        ]
    if work_type == "backend-code":
        return base + [
            {
                "artifact_type": "build-diff",
                "path": "build-iter-1.diff",
                "producer_role": "build",
                "required": True,
            },
            {
                "artifact_type": "unit-test-log",
                "path": "unit-iter-1.log",
                "producer_role": "test-unit",
                "required": False,
            },
        ]
    if work_type == "ui":
        return base + [
            {
                "artifact_type": "smoke-output",
                "path": "smoke-test-output.txt",
                "producer_role": "deploy-smoke",
                "required": True,
            },
            {
                "artifact_type": "demo-video",
                "path": "review-artifacts/demo.mp4",
                "producer_role": "demo-video",
                "required": False,
            },
        ]
    return base + [
        {
            "artifact_type": "baseline",
            "path": "baseline.txt",
            "producer_role": "baseline",
            "required": False,
        },
        {
            "artifact_type": "verification-report",
            "path": "verification-report-iter-1.md",
            "producer_role": "verify",
            "required": True,
        },
        {
            "artifact_type": "final-test-log",
            "path": "final-tests.txt",
            "producer_role": "full-test-gate",
            "required": False,
        },
    ]


def _artifact_entry(run_dir: Path, spec: dict[str, object]) -> dict[str, object]:
    rel_path = str(spec["path"])
    path = run_dir / rel_path
    exists = path.exists()
    required = bool(spec["required"])
    status = "present" if exists else "missing" if required else "skipped"
    return {
        "artifact_type": spec["artifact_type"],
        "path": rel_path,
        "producer_role": spec["producer_role"],
        "required": required,
        "status": status,
    }


def write_artifact_manifest(
    run_dir: Path,
    *,
    complexity: str,
    is_docs_only: bool,
    has_ui: bool,
) -> Path:
    work_type = classify_work_type(
        complexity=complexity,
        is_docs_only=is_docs_only,
        has_ui=has_ui,
    )
    manifest_path = run_dir / ARTIFACT_MANIFEST
    payload = {
        "contract_version": 1,
        "work_type": work_type,
        "run_dir": str(run_dir),
        "locations": {
            "verdicts": VERDICTS_DIR,
            "review_artifacts": REVIEW_ARTIFACTS_DIR,
            "transcripts": TRANSCRIPTS_DIR,
        },
        "artifacts": [
            _artifact_entry(run_dir, spec)
            for spec in _artifact_specs(work_type)
        ],
    }
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return manifest_path


def ensure_handoff_summary(
    run_dir: Path,
    *,
    issue_id: str,
    complexity: str,
    is_docs_only: bool,
    has_ui: bool,
) -> Path:
    summary_path = run_dir / REVIEW_SUMMARY
    if summary_path.exists():
        return summary_path
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    work_type = classify_work_type(
        complexity=complexity,
        is_docs_only=is_docs_only,
        has_ui=has_ui,
    )
    summary_path.write_text(
        "\n".join(
            [
                "# Handoff Summary",
                "",
                f"- Issue: `{issue_id}`",
                f"- Work type: `{work_type}`",
                f"- Run dir: `{run_dir}`",
                f"- Manifest: `{run_dir / ARTIFACT_MANIFEST}`",
                "",
                "Open this summary first, then follow the manifest for the rest of the proof set.",
                "",
            ]
        )
    )
    return summary_path
