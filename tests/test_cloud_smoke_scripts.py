"""Static checks for scripts/cloud-smoke/ — prefect-orchestration-tyf.5.

Cheap parse + (optional) shellcheck pass. Skips gracefully when the
optional tools are missing so default CI doesn't gate on them.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_DIR = REPO_ROOT / "scripts" / "cloud-smoke"

EXPECTED_SCRIPTS = {
    "lib.sh",
    "provision-kind.sh",
    "provision-hetzner.sh",
    "seed-rig.sh",
    "seed-credentials.sh",
    "run-smoke.sh",
    "assert-success.sh",
    "teardown-kind.sh",
    "teardown-hetzner.sh",
}


def _scripts() -> list[Path]:
    return sorted(SMOKE_DIR.glob("*.sh"))


def test_smoke_dir_has_expected_scripts() -> None:
    present = {p.name for p in _scripts()}
    missing = EXPECTED_SCRIPTS - present
    assert not missing, f"missing smoke scripts: {sorted(missing)}"


@pytest.mark.parametrize("script", _scripts(), ids=lambda p: p.name)
def test_script_bash_parses(script: Path) -> None:
    res = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True, check=False
    )
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize("script", _scripts(), ids=lambda p: p.name)
def test_script_is_executable(script: Path) -> None:
    assert script.stat().st_mode & 0o111, f"{script.name} is not executable"


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not on PATH")
def test_shellcheck_clean() -> None:
    cmd = ["shellcheck", "--severity=warning", *(str(p) for p in _scripts())]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stdout + res.stderr


def test_run_smoke_help_lists_drivers() -> None:
    """`run-smoke.sh --help` should be usable without invoking docker/kind."""
    res = subprocess.run(
        [str(SMOKE_DIR / "run-smoke.sh"), "--help"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    assert res.returncode == 0, res.stderr
    out = res.stdout + res.stderr
    assert "SMOKE_DRIVER" in out
    assert "kind" in out and "hetzner" in out
