"""Shared fixtures for e2e CLI tests.

Every test gets its own `tmp_path` rig so concurrent test runs (and
concurrent pytest invocations across `software_dev_full` siblings) do
not contend on the real repo's `.beads/` or `.planning/`. Tests that
genuinely need the real installed `po` binary use the `po_runner`
fixture, which closes over the per-test tmp_path automatically.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Callable, Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolate_external_rig_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let an operator's selected rig leak into disposable e2e rigs."""
    monkeypatch.delenv("BEADS_DIR", raising=False)
    monkeypatch.delenv("PO_BEADS_BACKEND", raising=False)


@pytest.fixture
def po_bin() -> Path:
    """Path to the installed `po` script in the dev .venv. Skip if absent."""
    candidate = REPO_ROOT / ".venv" / "bin" / "po"
    if not candidate.exists():
        pytest.skip(f"po CLI not installed at {candidate}; run `uv sync` first")
    return candidate


@pytest.fixture(scope="session")
def prefect_api_url() -> Iterator[str]:
    """Run one isolated Prefect API for subprocess-driven flow roundtrips."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    binary = REPO_ROOT / ".venv" / "bin" / "prefect"
    process = subprocess.Popen(
        [str(binary), "server", "start", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "PREFECT_SERVER_ANALYTICS_ENABLED": "false"},
    )
    url = f"http://127.0.0.1:{port}/api"
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=0.5):
                break
        except Exception:
            if process.poll() is not None:
                pytest.skip("isolated Prefect server failed to start")
            time.sleep(0.25)
    else:
        process.terminate()
        pytest.skip("isolated Prefect server did not become healthy")
    yield url
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


@pytest.fixture
def po_runner(
    po_bin: Path, tmp_path: Path, prefect_api_url: str
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Return a callable that invokes `po <args>` in an isolated tmp rig.

    Default cwd is `tmp_path` so concurrent test runs don't fight over
    the real repo's `.beads/` / `.planning/`. PREFECT_API_URL is forced
    to a bogus URL so no test accidentally hits a real server even when
    the developer's shell exports a real one.
    """

    def _run(
        *args: str,
        cwd: Path | None = None,
        env_overrides: dict[str, str] | None = None,
        timeout: int = 60,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PREFECT_API_URL"] = prefect_api_url
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(
            [str(po_bin), *args],
            cwd=str(cwd or tmp_path),
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )

    return _run
