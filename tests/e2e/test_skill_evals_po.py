"""E2E test for `po run skill-evals --pack prefect-orchestration --skill po`.

Subprocesses the real `po` CLI against the in-tree `skills/po/` suite and
asserts that `reports/latest.json` is written and parseable.

We use `--dry-run` to avoid spending tokens / requiring API keys in CI:
- StubBackend returns deterministic ack output (no Claude CLI needed)
- Stub judge produces deterministic [0.5, 1.0) scores (no judge API key needed)

A real-judge run is the operator's responsibility before tagging a release.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_REPORTS_DIR = REPO_ROOT / "skills" / "po" / "reports"


def _po_bin() -> Path:
    candidate = REPO_ROOT / ".venv" / "bin" / "po"
    if not candidate.exists():
        pytest.skip(f"po CLI not installed at {candidate}; run `uv sync` first")
    return candidate


def test_skill_evals_po_dry_run_writes_report(tmp_path: Path) -> None:
    """`po run skill-evals --dry-run` produces a parseable verdict JSON.

    We back up `skills/po/reports/latest.json` first because the dry-run
    will overwrite it with stub-scored output, and the real run committed
    to git is the source of truth for the doctor check.
    """
    po_bin = _po_bin()
    backup_payload: bytes | None = None
    backup_md_payload: bytes | None = None
    json_path = SKILL_REPORTS_DIR / "latest.json"
    md_path = SKILL_REPORTS_DIR / "latest.md"
    skill_md_path = REPO_ROOT / "skills" / "po" / "SKILL.md"
    skill_md_backup = skill_md_path.read_bytes() if skill_md_path.exists() else None
    if json_path.exists():
        backup_payload = json_path.read_bytes()
    if md_path.exists():
        backup_md_payload = md_path.read_bytes()

    env = os.environ.copy()
    # `--dry-run` short-circuits both halves of the pipeline; ANTHROPIC_API_KEY
    # absence is irrelevant in that mode but we belt-and-suspenders unset it
    # to make the dry-run path obvious in logs.
    env.pop("ANTHROPIC_API_KEY", None)

    try:
        result = subprocess.run(
            [
                str(po_bin),
                "run",
                "skill-evals",
                "--pack",
                "prefect-orchestration",
                "--skill",
                "po",
                "--dry-run",
                "--tier",
                "smoke",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )

        assert result.returncode == 0, (
            f"po run skill-evals failed (exit {result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert json_path.is_file(), f"expected {json_path} to be written"

        # Verdict must be parseable + structurally valid.
        verdict = json.loads(json_path.read_text(encoding="utf-8"))
        assert verdict["pack"] == "prefect-orchestration"
        assert verdict["skill"] == "po"
        assert verdict["n_cases"] >= 1
        assert "n_passed" in verdict
        assert isinstance(verdict["cases"], list)
        # Smoke filter: only smoke-tier cases run.
        assert all(c["tier"] == "smoke" for c in verdict["cases"])
    finally:
        # Restore the committed reports + SKILL.md so this test doesn't
        # leave dirty working-tree changes on the host.
        if backup_payload is not None:
            json_path.write_bytes(backup_payload)
        elif json_path.exists():
            json_path.unlink()
        if backup_md_payload is not None:
            md_path.write_bytes(backup_md_payload)
        elif md_path.exists():
            md_path.unlink()
        if skill_md_backup is not None:
            skill_md_path.write_bytes(skill_md_backup)


@pytest.mark.skipif(
    shutil.which("claude") is None or os.environ.get("ANTHROPIC_API_KEY") is None,
    reason="real-judge run requires claude CLI on PATH and ANTHROPIC_API_KEY set",
)
def test_skill_evals_po_real_run_smoke_tier() -> None:
    """Optional: real-Claude smoke run. Skipped without claude CLI + API key.

    Mirrors the data-agent eval gating pattern. When invoked, exercises
    the full pipeline: real Claude case responses + real LLMJudge calls.
    """
    po_bin = _po_bin()
    backup_payload: bytes | None = None
    json_path = SKILL_REPORTS_DIR / "latest.json"
    if json_path.exists():
        backup_payload = json_path.read_bytes()

    try:
        result = subprocess.run(
            [
                str(po_bin),
                "run",
                "skill-evals",
                "--pack",
                "prefect-orchestration",
                "--skill",
                "po",
                "--tier",
                "smoke",
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert result.returncode == 0, result.stderr
        verdict = json.loads(json_path.read_text(encoding="utf-8"))
        # Smoke must hit 100% in a release-ready state.
        assert verdict["overall_pass"] is True
    finally:
        if backup_payload is not None:
            json_path.write_bytes(backup_payload)
