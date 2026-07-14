"""Real-beads integration proof for the dispatch tracker preflight."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from prefect_orchestration.cli import (
    DispatchTrackerMismatch,
    _validate_dispatch_tracker,
)


def _init_tracker(path: Path, prefix: str) -> None:
    path.mkdir()
    subprocess.run(
        ["bd", "init", "--prefix", prefix],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def test_real_trackers_reject_seed_missing_from_rig(tmp_path: Path) -> None:
    caller = tmp_path / "caller"
    rig = tmp_path / "rig"
    _init_tracker(caller, "caller")
    _init_tracker(rig, "rig")
    created = subprocess.run(
        ["bd", "create", "caller seed", "--json"],
        cwd=caller,
        check=True,
        capture_output=True,
        text=True,
    )
    bead_id = json.loads(created.stdout)["id"]

    with pytest.raises(DispatchTrackerMismatch) as raised:
        _validate_dispatch_tracker(
            {"issue_id": bead_id, "rig_path": str(rig)}, caller_path=caller
        )

    assert bead_id in str(raised.value)
    assert str(caller / ".beads") in str(raised.value)
    assert str(rig / ".beads") in str(raised.value)
