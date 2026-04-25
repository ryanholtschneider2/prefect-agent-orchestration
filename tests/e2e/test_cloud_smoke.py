"""Heavy e2e for the cloud smoke. Gated behind RUN_CLOUD_SMOKE=1.

The full smoke spins up kind / pulls images / runs Claude — too heavy
for default CI. The cheap test exercises `run-smoke.sh --dry-run` so
regressions in argument parsing / orchestrator wiring still surface
without provisioning anything.

prefect-orchestration-tyf.5.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_SMOKE = REPO_ROOT / "scripts" / "cloud-smoke" / "run-smoke.sh"


@pytest.mark.skipif(not RUN_SMOKE.exists(), reason="run-smoke.sh missing")
def test_run_smoke_dry_run_kind() -> None:
    """`--dry-run --kind` should walk the orchestrator without touching docker."""
    env = {**os.environ, "SMOKE_DRY": "1"}
    # Force a credential so seed-credentials.sh's pre-flight passes.
    env.setdefault("ANTHROPIC_API_KEY", "sk-dryrun-not-a-real-key")
    res = subprocess.run(
        [str(RUN_SMOKE), "--dry-run", "--kind"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env=env,
        timeout=60,
    )
    combined = res.stdout + res.stderr
    # Even on dry-run we don't require zero exit (some upstream tools
    # like `helm` or `kind` may not be installed). We DO require the
    # orchestrator to advance through its labelled phases until it hits
    # an external dep.
    assert "1/6 provision" in combined, combined
    # When all tools are present, dry-run should reach "5/6 trigger".
    if shutil.which("kind") and shutil.which("helm") and shutil.which("kubectl"):
        assert res.returncode == 0, combined


@pytest.mark.skipif(
    os.environ.get("RUN_CLOUD_SMOKE") != "1",
    reason="set RUN_CLOUD_SMOKE=1 to run the real cloud smoke (heavy)",
)
def test_run_smoke_full_kind() -> None:
    """Full smoke — provisions a real kind cluster. Manual operator gate."""
    res = subprocess.run(
        [str(RUN_SMOKE), "--kind"],
        check=False,
        cwd=REPO_ROOT,
        timeout=60 * 30,
    )
    assert res.returncode == 0
