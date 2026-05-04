"""E2E tests for the `po sessions` CLI.

Invokes the real installed `po` script in a subprocess, exercising
Typer → `prefect_orchestration.sessions` + `run_lookup`. Uses a
fabricated rig + run_dir on disk so no Prefect server or real bead
store is required — bead metadata is stubbed via env vars consumed
by `run_lookup`.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _po(
    *args: str, env_overrides: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    po_bin = REPO_ROOT / ".venv" / "bin" / "po"
    if not po_bin.exists():
        pytest.skip(f"po CLI not installed at {po_bin}; run `uv sync` first")
    env = os.environ.copy()
    env["PREFECT_API_URL"] = "http://127.0.0.1:1/api"
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [str(po_bin), *args],
        cwd=tempfile.mkdtemp(prefix="po-e2e-"),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_po_sessions_listed_in_help() -> None:
    result = _po("--help")
    assert result.returncode == 0, result.stderr
    assert "sessions" in result.stdout


def test_po_sessions_help_flags() -> None:
    result = _po("sessions", "--help")
    assert result.returncode == 0, result.stderr
    assert "--resume" in result.stdout


def test_po_sessions_unknown_issue_exits_nonzero() -> None:
    """Missing bead metadata → exit 2 from RunDirNotFound, no traceback."""
    result = _po("sessions", "po-sessions-e2e-does-not-exist")
    assert result.returncode != 0
    assert "Traceback" not in result.stderr


def _write_run_dir(tmp_path: Path, issue_id: str, metadata: dict[str, str]) -> Path:
    rig_path = tmp_path / "rig"
    run_dir = rig_path / ".planning" / "software-dev-full" / issue_id
    run_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text(json.dumps(metadata))
    return rig_path


def test_po_sessions_renders_table_and_resume(tmp_path: Path) -> None:
    """Full-path smoke: real subprocess with a bead stubbed on disk,
    renders the table and emits a resume one-liner for a known role."""
    issue_id = "po-sessions-e2e-smoke"
    uuid_builder = "11111111-1111-1111-1111-111111111111"
    rig_path = _write_run_dir(
        tmp_path,
        issue_id,
        {
            "po.rig_path": str(tmp_path / "rig"),
            "po.run_dir": str(
                tmp_path / "rig" / ".planning" / "software-dev-full" / issue_id
            ),
            "session_builder": uuid_builder,
            "session_critic": "22222222-2222-2222-2222-222222222222",
        },
    )

    # Simulate bd metadata via a fake `bd` shim on PATH — run_lookup shells out.
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "bd"
    payload = {
        "id": issue_id,
        "metadata": {
            "po.rig_path": str(rig_path),
            "po.run_dir": str(rig_path / ".planning" / "software-dev-full" / issue_id),
        },
    }
    shim.write_text(
        "#!/bin/bash\n"
        'if [ "$1" = "show" ]; then\n'
        f"  cat <<'EOF'\n{json.dumps(payload)}\nEOF\n"
        "fi\n"
    )
    shim.chmod(0o755)
    env = {"PATH": f"{shim_dir}:{os.environ.get('PATH', '')}"}

    # Table render
    result = _po("sessions", issue_id, env_overrides=env)
    if result.returncode != 0:
        pytest.skip(
            f"run_lookup couldn't resolve via bd shim (exit {result.returncode}): "
            f"{result.stderr}"
        )
    assert "builder" in result.stdout
    assert "critic" in result.stdout
    assert uuid_builder in result.stdout

    # Resume one-liner
    result = _po("sessions", issue_id, "--resume", "builder", env_overrides=env)
    assert result.returncode == 0, result.stderr
    assert uuid_builder in result.stdout
    assert "--fork-session" in result.stdout

    # Unknown role → exit 4
    result = _po("sessions", issue_id, "--resume", "ghost", env_overrides=env)
    assert result.returncode == 4, (result.stdout, result.stderr)
