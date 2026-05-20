"""Verdict artifact I/O — bd-metadata-backed.

Agents end their turn by stamping a structured payload onto the iter
bead's metadata at key ``po.<name>``, then closing the bead with a
canonical close-reason. The orchestrator reads bd as the source of
truth — no verdict files, no prose parsing. Two wins:

  1. Single source of truth. bd already knows when the work is done
     (status=closed); reading metadata off the same bead means there
     is no second artifact that can disagree with bd's state.
  2. The bd state-change hooks (`-nanocorps` fork) can react to
     metadata writes uniformly — the orchestration substrate gets
     observability for free.

Keys are namespaced (``po.triage``, ``po.full_test_gate``,
``po.ralph``, …) so a single iter bead can carry both run-dir
bookkeeping (``po.run_dir``, ``po.rig_path``) and the role's verdict
without collision.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


def read_bead_verdict(
    bead_id: str,
    name: str,
    *,
    rig_path: Path | str | None = None,
) -> dict[str, Any]:
    """Read a verdict stamped on bead ``bead_id`` at metadata key ``po.<name>``.

    Shells ``bd show <bead_id> --json`` and returns the parsed value at
    ``metadata.po.<name>``. The value is whatever the agent wrote via
    ``bd update <bead_id> --metadata '{"po.<name>": {...}}'`` — usually
    a JSON object, but scalars work too.

    Raises ``FileNotFoundError`` when the bead doesn't exist, and
    ``KeyError`` if the bead exists but lacks the expected metadata key
    (agent skipped the metadata stamp — usually a prompt regression).
    """
    proc = subprocess.run(
        ["bd", "show", bead_id, "--json"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(rig_path) if rig_path is not None else None,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise FileNotFoundError(
            f"bd show {bead_id} returned no rows "
            f"(rc={proc.returncode}, stderr={proc.stderr[:200]!r})"
        )
    try:
        rows = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"bd show {bead_id} --json was not parseable:\n{proc.stdout[:500]}"
        ) from exc
    issue = rows[0] if isinstance(rows, list) else rows
    if not isinstance(issue, dict):
        raise FileNotFoundError(f"bd show {bead_id} returned no issue rows")
    metadata = issue.get("metadata") or {}
    key = f"po.{name}"
    if key not in metadata:
        raise KeyError(
            f"bead {bead_id} has no metadata key {key!r}. "
            f"Agent likely skipped the `bd update ... --metadata '{{\"{key}\": ...}}'` step. "
            f"Available keys: {sorted(metadata.keys())}"
        )
    value = metadata[key]
    # `--set-metadata k=v` stores values as strings, even if v looks like JSON.
    # `--metadata '<json>'` stores native objects. Accept both shapes — if the
    # stored value is a string that parses as JSON, decode it for the caller.
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {"value": value}
    if isinstance(value, dict):
        return value
    return {"value": value}


def prompt_for_bead_verdict(
    sess: Any,
    prompt: str,
    bead_id: str,
    name: str,
    *,
    fork: bool = False,
    rig_path: Path | str | None = None,
) -> dict[str, Any]:
    """Send ``prompt`` through ``sess`` and read the resulting bead-metadata verdict.

    The agent is expected to end its turn with::

        bd update <bead_id> --metadata '{"po.<name>": {...}}'
        bd close  <bead_id> --reason "<keyword>: ..."

    On return, this function reads ``po.<name>`` off the bead and
    returns it. The agent's prose reply is discarded.

    When ``PO_RESUME=1`` is set in the environment AND the bead
    already carries ``po.<name>``, the agent is NOT prompted — the
    existing metadata is returned. This is the bd-backed resume
    fast-path.
    """
    if os.environ.get("PO_RESUME") == "1":
        try:
            return read_bead_verdict(bead_id, name, rig_path=rig_path)
        except (FileNotFoundError, KeyError):
            pass
    if fork:
        sess.prompt(prompt, fork=True)
    else:
        sess.prompt(prompt)
    return read_bead_verdict(bead_id, name, rig_path=rig_path)
