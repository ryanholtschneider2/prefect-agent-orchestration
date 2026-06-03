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
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Delays (seconds) between retry attempts. Index 0 = before attempt 1, index 1 = before attempt 2.
_RETRY_DELAYS: list[float] = [0.5, 1.0]
_RETRY_TIMEOUT: int = 10

# Process-local cache keyed on (bead_id, name). Populated on every successful read.
# Returned (with a warning) when all bd retries are exhausted.
_verdict_cache: dict[tuple[str, str], dict] = {}


def _bd_show_once(
    bead_id: str,
    name: str,
    *,
    rig_path: Path | str | None,
    timeout: int,
) -> dict[str, Any]:
    """Single attempt to read ``po.<name>`` from bead ``bead_id``.

    Raises ``FileNotFoundError`` or ``ValueError`` on bd/parse failures.
    Raises ``KeyError`` when the bead exists but lacks the key (semantic
    failure — callers must not retry this).
    Raises ``subprocess.TimeoutExpired`` when bd takes longer than ``timeout``.
    """
    proc = subprocess.run(
        ["bd", "show", bead_id, "--json"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(rig_path) if rig_path is not None else None,
        timeout=timeout,
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

    Retries up to 3 times (with ``_RETRY_DELAYS`` backoff and a per-attempt
    timeout) on transient bd failures. On exhausted retries, returns a
    cached verdict from a prior successful read if available. ``KeyError``
    (missing metadata key) is never retried — it is a semantic failure.
    """
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            result = _bd_show_once(
                bead_id, name, rig_path=rig_path, timeout=_RETRY_TIMEOUT
            )
            _verdict_cache[(bead_id, name)] = result
            return result
        except KeyError:
            raise
        except (FileNotFoundError, ValueError, subprocess.TimeoutExpired, OSError) as exc:
            last_exc = exc
            logger.warning(
                "read_bead_verdict: attempt %d/3 failed for %s.%s: %s",
                attempt + 1,
                bead_id,
                name,
                str(exc)[:200],
            )
            if attempt < len(_RETRY_DELAYS):
                time.sleep(_RETRY_DELAYS[attempt])

    cached = _verdict_cache.get((bead_id, name))
    if cached is not None:
        logger.warning(
            "read_bead_verdict: bd unreachable after 3 attempts for %s.%s; returning cached verdict",
            bead_id,
            name,
        )
        return cached
    raise last_exc  # type: ignore[misc]


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
