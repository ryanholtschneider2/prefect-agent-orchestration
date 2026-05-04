"""Claude Code `Stop` hook: signal turn-end to the orchestrator.

When Claude Code finishes processing a turn (no more pending tool calls,
no more thinking), the `Stop` hook fires. We write a per-session
sentinel file the `TmuxInteractiveClaudeBackend` polls for so it knows
the role's turn is done — the agent can keep the TUI alive (typing
`/exit` is not necessary) and the orchestrator just kills the tmux
session once it has the signal.

Hook input arrives as JSON on stdin. We read `session_id` and touch
`<stop_dir>/<session_id>.stopped` (default `~/.cache/po-stops/`).

Configured per-rig in `<cwd>/.claude/settings.json` by the backend on
each role spawn — see TmuxInteractiveClaudeBackend.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def stop_dir() -> Path:
    base = os.environ.get("PO_STOP_DIR")
    if base:
        return Path(base)
    return Path.home() / ".cache" / "po-stops"


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        # Hook gets called in shapes we don't model — never block claude.
        return
    sid = (data.get("session_id") or "").strip()
    if not sid:
        return
    out = stop_dir()
    out.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            k: v
            for k, v in data.items()
            if isinstance(v, (str, int, float, bool, type(None)))
        }
    )
    (out / f"{sid}.stopped").write_text(payload)


if __name__ == "__main__":
    main()
