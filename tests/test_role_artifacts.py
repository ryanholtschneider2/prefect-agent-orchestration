from __future__ import annotations

from pathlib import Path

import pytest

import prefect_orchestration.role_artifacts as mod


def test_publish_run_artifacts_publishes_summary_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    review_dir = run_dir / "review-artifacts"
    review_dir.mkdir(parents=True)
    summary = review_dir / "summary.md"
    summary.write_text("# Summary\n")
    manifest = run_dir / "artifact-manifest.json"
    manifest.write_text('{"contract_version": 1}\n')

    calls: list[dict[str, str]] = []

    def fake_create_markdown_artifact(
        *,
        key: str,
        markdown: str,
        description: str,
    ) -> None:
        calls.append({"key": key, "markdown": markdown, "description": description})

    monkeypatch.setattr(mod, "create_markdown_artifact", fake_create_markdown_artifact)

    mod.publish_run_artifacts(
        run_dir,
        ["review-artifacts/summary.md", "artifact-manifest.json"],
        issue_id="prefect-orchestration-eu8",
    )

    assert [call["description"] for call in calls] == [
        "Run artifact: review-artifacts/summary.md",
        "Run artifact: artifact-manifest.json",
    ]
    assert all(
        call["key"].startswith("prefect-orchestration-eu8-run-artifact-")
        for call in calls
    )
