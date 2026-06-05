"""Run-dir-scoped map: role-step *convention id* → *backend-assigned* bead id.

The orchestrator names each role-step by the convention
``<seed>.<step>.iter<N>``. On dolt (``bd``) that id is honored verbatim by
``bd create --id=…``, so the convention id IS the real bead id and this map
is unused (every ``lookup`` misses and the caller falls back to the
convention id). On br (``beads_rust``) ``create`` has no ``--id`` flag — br
mints its own flat id — so the convention id is a *phantom* that no
``br show`` can resolve.

`agent_step` is stateless across calls: without a persisted mapping, each
re-entry for the same role-step recomputes the phantom convention id, the
fast-path cache check misses, and ``create_child_bead`` mints *another*
fresh bead — so already-completed iters get re-dispatched forever and the
agent (whose real bead is already closed) is re-nudged about a bead that
doesn't exist. Recording the real id under the convention key on first
create lets every later call (re-entry, the convergence ladder, the
CONTEXT.md bundle) resolve the bead that actually exists.

The map lives at ``<run_dir>/iter-bead-ids.json``. It is a best-effort
fast-path optimization, never a correctness dependency: a missing or
corrupt map simply means the next call re-probes / re-creates.
"""

from __future__ import annotations

import json
from pathlib import Path

MAP_FILENAME = "iter-bead-ids.json"


def convention_id(seed_id: str, step: str, iter_n: int) -> str:
    """The orchestrator's stable name for a role-step: ``<seed>.<step>.iterN``."""
    return f"{seed_id}.{step}.iter{iter_n}"


def lookup(run_dir: Path | str, convention_key: str) -> str | None:
    """Return the backend-assigned id recorded for *convention_key*, or None.

    None on a fresh run-dir (first call), a dolt rig (nothing recorded), or
    an unreadable/corrupt map — callers fall back to the convention id.
    """
    try:
        data = json.loads((Path(run_dir) / MAP_FILENAME).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    val = data.get(convention_key)
    return val if isinstance(val, str) and val else None


def record(run_dir: Path | str, convention_key: str, bead_id: str) -> None:
    """Persist ``convention_key -> bead_id`` (best-effort read-modify-write).

    No-ops when the mapping is already present and unchanged. Swallows I/O
    errors — the map is an optimization, not a correctness dependency.
    """
    path = Path(run_dir) / MAP_FILENAME
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    if data.get(convention_key) == bead_id:
        return
    data[convention_key] = bead_id
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True))
    except OSError:
        pass
