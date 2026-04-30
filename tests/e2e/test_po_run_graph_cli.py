"""E2E test for `po run graph` (prefect-orchestration-uc0, AC 10).

Spawns a temp rig with `bd init`, creates 3 synthetic beads with
explicit `bd dep` edges (no dot-suffix naming), runs `po run graph
<root> --dry-run` against the live `po` binary, and asserts that all 3
descendants are picked up and submitted in topo order.

The flow uses `--formula=software-dev-full` (the default) but with
`--dry-run` so the per-node sub-flow uses `StubBackend` and writes
fake verdict files without spawning Claude. We then read the `verdicts/`
directories under the per-issue run_dirs to confirm the dispatch order.

Skipped by default in this rig (`PO_SKIP_E2E=1` in `.po-env`); run
manually with `uv run python -m pytest tests/e2e/test_po_run_graph_cli.py`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import pytest


pytestmark = pytest.mark.skipif(
    not shutil.which("bd"),
    reason="bd CLI not on PATH; e2e graph test requires a real beads install",
)


def _bd_in(
    rig: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bd", *args],
        cwd=str(rig),
        capture_output=True,
        text=True,
        check=check,
        env={**os.environ, "BEADS_ACTOR": "uc0-e2e"},
    )


def test_po_run_graph_discovers_via_bd_dep_edges(
    po_runner: Callable[..., subprocess.CompletedProcess[str]],
    tmp_path: Path,
) -> None:
    rig = tmp_path / "rig"
    rig.mkdir()

    # Embedded dolt is fine for an isolated test rig — no concurrent
    # writers, no other processes touching this `.beads/`.
    init = _bd_in(rig, "init", check=False)
    if init.returncode != 0:
        pytest.skip(f"bd init failed: {init.stderr}")

    # Create one root + three children with NO dot-suffix naming.
    # `bd create --json` is the only way to get the id back in a parseable
    # form — `--quiet` still prints the human-readable creation banner.
    def _create(title: str, kind: str = "task") -> str:
        proc = _bd_in(
            rig,
            "create",
            f"--type={kind}",
            f"--title={title}",
            "--priority=2",
            "--json",
        )
        return json.loads(proc.stdout)["id"]

    root_id = _create("root grouping bead", kind="feature")
    a_id = _create("A")
    b_id = _create("B")
    c_id = _create("C")

    # Edges (`bd dep add <issue> <depends-on>`):
    #   A blocked-by root  → A appears in BFS-up from root
    #   B blocked-by A     → B appears via A
    #   C blocked-by A,B   → C appears via A and orders after B
    _bd_in(rig, "dep", "add", a_id, root_id)
    _bd_in(rig, "dep", "add", b_id, a_id)
    _bd_in(rig, "dep", "add", c_id, a_id)
    _bd_in(rig, "dep", "add", c_id, b_id)

    # Sanity: bd dep list <root> --direction=up sees A.
    up = _bd_in(rig, "dep", "list", root_id, "--direction=up", "--json")
    assert a_id in up.stdout, up.stdout

    # Run graph_run via the installed `po`. `--dry-run` flips the
    # per-node software_dev_full sub-flow to StubBackend, skipping
    # real Claude calls. We override the conftest's bogus
    # `PREFECT_API_URL=http://127.0.0.1:1` because graph_run actually
    # needs a Prefect API to record flow state — point at the live
    # local server (started via `prefect server start`); skip if
    # unreachable.
    import urllib.request

    api_url = "http://127.0.0.1:4200/api"
    try:
        with urllib.request.urlopen(f"{api_url}/health", timeout=2):
            pass
    except Exception:
        pytest.skip(f"Prefect server not reachable at {api_url}")

    try:
        result = po_runner(
            "run",
            "graph",
            "--root-id",
            root_id,
            "--rig",
            "uc0-e2e",
            "--rig-path",
            str(rig),
            "--dry-run",
            "--traverse=blocks",
            cwd=rig,
            env_overrides={"PREFECT_API_URL": api_url},
            timeout=180,
        )

        # The flow may legitimately exit non-zero if any per-node sub-flow
        # raises (StubBackend smoke errors etc.); what we care about is
        # that the BFS-collected nodes line up with what we built.
        combined = result.stdout + "\n" + result.stderr
        assert a_id in combined, combined
        assert b_id in combined, combined
        assert c_id in combined, combined
        # Topo order check: A's submission line must appear before B's
        # which must appear before C's. The `_dispatch_nodes` info-log
        # prints `submitting N node(s): [<id>, ...]` in topo order.
        submit_line = next(
            (
                ln
                for ln in combined.splitlines()
                if "submitting" in ln and "node(s)" in ln
            ),
            None,
        )
        assert submit_line is not None, combined
        assert (
            submit_line.index(a_id) < submit_line.index(b_id) < submit_line.index(c_id)
        ), submit_line
    finally:
        # Clean up any Prefect flow runs created by this test. Without
        # this, a `po run graph` whose subprocess crashes mid-flight
        # leaves Running flows that linger in the Prefect DB pointing at
        # a temp `rig_path` that pytest is about to delete (the leaked-
        # zombie scenario observed 2026-04-29: `po status` shows
        # `rig-XYZ` rows for hours after the tests that created them
        # exited). Identifies our flow runs by the `rig_path` param
        # starting with this test's tmp `rig` dir — unique per pytest
        # invocation.
        _cancel_flow_runs_for_rig(api_url, str(rig))


def _cancel_flow_runs_for_rig(api_url: str, rig_path: str) -> None:
    """Best-effort: cancel any non-terminal Prefect flow runs whose
    `rig_path` parameter starts with `rig_path`. Swallows all errors —
    this runs in a finally block, so a failure here must never replace
    the original test failure (pytest semantics)."""
    try:
        import os as _os

        import anyio
        from prefect.client.orchestration import get_client
        from prefect.client.schemas.filters import (
            FlowRunFilter,
            FlowRunFilterState,
            FlowRunFilterStateType,
        )
        from prefect.client.schemas.objects import StateType
        from prefect.states import Cancelled

        old_url = _os.environ.get("PREFECT_API_URL")
        _os.environ["PREFECT_API_URL"] = api_url

        async def _run() -> None:
            async with get_client() as c:
                runs = await c.read_flow_runs(
                    flow_run_filter=FlowRunFilter(
                        state=FlowRunFilterState(
                            type=FlowRunFilterStateType(
                                any_=[
                                    StateType.RUNNING,
                                    StateType.PENDING,
                                    StateType.SCHEDULED,
                                ]
                            ),
                        ),
                    ),
                    limit=200,
                )
                for r in runs:
                    rp = (r.parameters or {}).get("rig_path", "")
                    if rp.startswith(rig_path):
                        await c.set_flow_run_state(
                            r.id,
                            Cancelled(message=f"test cleanup: {rig_path} torn down"),
                        )

        try:
            anyio.run(_run)
        finally:
            if old_url is None:
                _os.environ.pop("PREFECT_API_URL", None)
            else:
                _os.environ["PREFECT_API_URL"] = old_url
    except Exception:
        # Best-effort: don't let cleanup hide the test result.
        pass
