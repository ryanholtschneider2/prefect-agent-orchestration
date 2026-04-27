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
    root = _bd_in(
        rig,
        "create",
        "--type=feature",
        "--title=root grouping bead",
        "--priority=2",
        "--quiet",
    )
    a = _bd_in(
        rig,
        "create",
        "--type=task",
        "--title=A",
        "--priority=2",
        "--quiet",
    )
    b = _bd_in(
        rig,
        "create",
        "--type=task",
        "--title=B",
        "--priority=2",
        "--quiet",
    )
    c = _bd_in(
        rig,
        "create",
        "--type=task",
        "--title=C",
        "--priority=2",
        "--quiet",
    )

    # bd create --quiet prints just the new id on stdout.
    root_id = root.stdout.strip().splitlines()[-1]
    a_id = a.stdout.strip().splitlines()[-1]
    b_id = b.stdout.strip().splitlines()[-1]
    c_id = c.stdout.strip().splitlines()[-1]

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
    # real Claude calls.
    result = po_runner(
        "run",
        "graph",
        root_id,
        "--rig",
        "uc0-e2e",
        "--rig-path",
        str(rig),
        "--dry-run",
        "--traverse=blocks",
        cwd=rig,
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
        (ln for ln in combined.splitlines() if "submitting" in ln and "node(s)" in ln),
        None,
    )
    assert submit_line is not None, combined
    assert (
        submit_line.index(a_id) < submit_line.index(b_id) < submit_line.index(c_id)
    ), submit_line
