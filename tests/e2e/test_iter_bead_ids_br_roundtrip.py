"""Real-`br` close-the-loop for prefect-orchestration-99k.

The bug: on br (beads_rust) flat-id rigs, `agent_step` re-entry recomputed
the dotted convention id `<seed>.<step>.iterN` — a *phantom* that no `br show`
can resolve — missed the fast-path cache, and re-minted a fresh iter bead on
every call, so already-completed iters got re-dispatched forever and the agent
was re-nudged about a non-existent bead.

The fix persists a convention->real-id map (`iter_bead_ids`) in the run-dir
and resolves through it before the fast-path probe. These tests exercise the
*real* `br` binary against a *real* rig (no mocks) to confirm the symptom is
gone end-to-end:

1. br mints its own flat id that differs from the dotted convention id;
2. the dotted convention id is genuinely a phantom (`br show` can't resolve it);
3. re-entry resolves the recorded real id — so the closed-bead cache check hits
   and `create_child_bead` is NOT called again (no re-mint, iter count stays 1).

Skipped when `br` is not on PATH (unit-layer mocks in
`tests/test_agent_step.py` / `tests/test_iter_bead_ids.py` cover the logic
without the binary).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from prefect_orchestration import iter_bead_ids
from prefect_orchestration.beads_backend import resolve_backend
from prefect_orchestration.beads_meta import close_issue, create_child_bead

pytestmark = pytest.mark.skipif(
    shutil.which("br") is None,
    reason="br (beads_rust) not on PATH; skipping real-br e2e",
)


def _br(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["br", *args, "--allow-stale"],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        check=False,
    )


@pytest.fixture
def br_rig(tmp_path: Path) -> Path:
    """A freshly `br init`-ed rig with one open seed bead."""
    rig = tmp_path / "br-rig"
    rig.mkdir()
    init = _br("init", "--prefix", "bd", "--actor", "tester", cwd=rig)
    assert init.returncode == 0, f"br init failed: {init.stderr}{init.stdout}"
    return rig


def _seed(rig: Path) -> str:
    proc = _br("create", "seed feature", "-t", "feature", "-p", "2", "--silent", cwd=rig)
    assert proc.returncode == 0, f"br create seed failed: {proc.stderr}{proc.stdout}"
    return proc.stdout.strip()


def test_br_reentry_resolves_real_id_no_remint(br_rig: Path) -> None:
    """Full create -> record -> re-entry cycle against the real br binary."""
    seed = _seed(br_rig)
    assert resolve_backend(br_rig) == "br"

    run_dir = br_rig / ".planning" / "agent-step" / seed
    run_dir.mkdir(parents=True, exist_ok=True)

    conv = iter_bead_ids.convention_id(seed, "build", 1)
    # The dotted convention id is a phantom on br: nothing resolves it.
    phantom = _br("show", conv, cwd=br_rig)
    assert phantom.returncode != 0 or conv not in (phantom.stdout or "")

    # First call: agent_step's create path mints a real (flat) id, then records.
    real_id = create_child_bead(
        seed, conv, title="build iter1", description="x", rig_path=br_rig
    )
    assert real_id != conv, "br should mint its own flat id, not honor the convention id"
    iter_bead_ids.record(run_dir, conv, real_id)

    # The agent finishes iter1 and closes its real bead.
    close_issue(real_id, "complete: built", rig_path=br_rig)

    # Re-entry: agent_step resolves target_bead through the map BEFORE probing.
    resolved = iter_bead_ids.lookup(run_dir, conv) or conv
    assert resolved == real_id, "re-entry must resolve the recorded real id, not the phantom"

    # The resolved (real) bead reads closed -> fast-path cache short-circuits ->
    # create_child_bead is never reached again. Confirm via the real binary.
    show = _br("show", resolved, "--json", cwd=br_rig)
    assert show.returncode == 0, f"br show {resolved} failed: {show.stderr}"
    body = json.loads(show.stdout)
    body = body[0] if isinstance(body, list) else body
    assert body.get("status") == "closed"

    # No re-mint: the rig holds exactly the seed + one iter bead (here: 2),
    # never a growing pile of phantom-triggered duplicates.
    listing = _br("list", "--all", "--json", cwd=br_rig)
    assert listing.returncode == 0, f"br list failed: {listing.stderr}"
    ids = sorted(b["id"] for b in json.loads(listing.stdout))
    assert ids == sorted([seed, real_id]), f"expected exactly seed+iter, got {ids}"


def test_br_map_survives_fresh_process_no_remint(br_rig: Path) -> None:
    """The map is on-disk, so a *new* call (stateless agent_step) re-reads it.

    This is the actual failure mode: agent_step is stateless across Prefect task
    invocations. Recording to disk is what makes the second invocation idempotent.
    """
    seed = _seed(br_rig)
    run_dir = br_rig / ".planning" / "agent-step" / seed
    run_dir.mkdir(parents=True, exist_ok=True)
    conv = iter_bead_ids.convention_id(seed, "build", 1)

    real_id = create_child_bead(
        seed, conv, title="build iter1", description="x", rig_path=br_rig
    )
    iter_bead_ids.record(run_dir, conv, real_id)

    # Simulate a brand-new process: nothing in memory, only the on-disk map.
    reread = iter_bead_ids.lookup(run_dir, conv)
    assert reread == real_id

    # Because the lookup hits, the stateless re-entry targets the existing bead
    # and does NOT call create_child_bead again. Prove the negative: only one
    # iter bead exists under the seed.
    listing = _br("list", "--all", "--json", cwd=br_rig)
    iter_beads = [
        b["id"] for b in json.loads(listing.stdout) if b["id"] not in (seed,)
    ]
    assert iter_beads == [real_id], f"exactly one iter bead expected, got {iter_beads}"
