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
import os
from pathlib import Path
from typing import Any


def verdicts_dir(run_dir: Path) -> Path:
    d = run_dir / "verdicts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def prompt_for_verdict(
    sess: Any,
    prompt: str,
    run_dir: Path,
    name: str,
    *,
    fork: bool = False,
) -> dict[str, Any]:
    """Send ``prompt`` through ``sess`` and read the resulting verdict file.

    Convenience helper used by formula packs: every step that ends with
    a verdict has the same shape — prompt the agent, then read
    ``<run_dir>/verdicts/<name>.json``. ``fork`` forwards to
    ``AgentSession.prompt(fork_session=...)`` so callers can opt into a
    forked turn that doesn't bump the parent session's resume UUID.

    The agent's textual reply is discarded — only the verdict file
    matters (per parsing.py docstring: file artifacts beat prose
    parsing). ``read_verdict`` raises ``FileNotFoundError`` if the
    agent skipped writing the file.

    When ``PO_RESUME=1`` is set in the environment AND a verdict file
    for ``name`` already exists, the agent is NOT prompted — the
    existing verdict is read and returned. This is what `po resume`
    relies on to skip already-completed steps when relaunching a flow.
    """
    expected = verdicts_dir(run_dir) / f"{name}.json"
    if os.environ.get("PO_RESUME") == "1" and expected.exists():
        return read_verdict(run_dir, name)
    try:
        if fork:
            sess.prompt(prompt, fork_session=True, expect_verdict=expected)
        else:
            sess.prompt(prompt, expect_verdict=expected)
    except TypeError:
        # Stub sessions used in older tests don't accept expect_verdict;
        # fall through and let read_verdict's FileNotFoundError surface.
        if fork:
            sess.prompt(prompt, fork_session=True)
        else:
            sess.prompt(prompt)
    return read_verdict(run_dir, name)


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
        raise ValueError(
            f"verdict file {path} is not valid JSON:\n{text[:500]}"
        ) from exc
