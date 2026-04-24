"""Verdict artifact I/O.

Agents end their turn by writing a small JSON file to
`$RUN_DIR/verdicts/<name>.json`. The orchestrator reads that file to
decide the next edge. Two wins over parsing the agent's prose:

  1. The file is a side-effect we can observe deterministically —
     no regex over free-form output, no "the LLM decided to add a
     second JSON block for illustration."
  2. The rest of the reply is free to be natural prose / commits /
     file writes. The agent isn't being shoehorned into a format it
     wouldn't otherwise pick.

Verdict filenames are stable (e.g. `triage.json`, `review-iter-2.json`,
`ralph-iter-1.json`) so a crashed-and-resumed flow can detect prior
verdicts without reading transcripts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def verdicts_dir(run_dir: Path) -> Path:
    d = run_dir / "verdicts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_verdict(run_dir: Path, name: str) -> dict[str, Any]:
    """Read a verdict file the agent just wrote.

    Raises FileNotFoundError if the agent skipped writing it — that's
    useful; we want the flow to fail loudly on missing artifacts rather
    than silently proceed on a default verdict.
    """
    path = verdicts_dir(run_dir) / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"agent did not write verdict file {path}. "
            f"Check the task's prompt ends with the `echo ... > {path}` instruction."
        )
    text = path.read_text().strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"verdict file {path} is not valid JSON:\n{text[:500]}") from exc
