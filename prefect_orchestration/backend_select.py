"""Default agent-runtime backend picker.

Single seam used by packs and ad-hoc scripts to choose between
`TmuxClaudeBackend`, `ClaudeCliBackend`, and `StubBackend`. The pack-side
default in `software_dev.py` already does `shutil.which("tmux")`; this
helper hardens it by also requiring stdout to be a TTY, which matters
inside containers where tmux is installed but no terminal is attached.

Honors `PO_BACKEND=cli|tmux|stub` as an explicit override. `tmux` without
a tmux binary on PATH raises (no silent fallback when the user asked for
tmux on purpose).
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Literal, Type

from prefect_orchestration.agent_session import (
    ClaudeCliBackend,
    SessionBackend,
    StubBackend,
    TmuxClaudeBackend,
)

BackendChoice = Literal["cli", "tmux", "stub", "auto"]


def _stdout_is_tty() -> bool:
    """Return True iff stdout is attached to a TTY (`isatty()`)."""
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def select_default_backend(
    *,
    override: str | None = None,
    have_tmux: bool | None = None,
    is_tty: bool | None = None,
) -> Type[SessionBackend]:
    """Pick a backend factory.

    Args:
      override:    explicit `PO_BACKEND` value. Defaults to the env var
                   when None; pass `""` to ignore env entirely.
      have_tmux:   inject a fake `shutil.which('tmux')` result (testing).
      is_tty:      inject a fake `sys.stdout.isatty()` result (testing).

    Raises:
      RuntimeError: when `PO_BACKEND=tmux` is set but no tmux binary is
                    on PATH. Refuses to silently fall back when the user
                    explicitly asked for tmux.
    """
    choice = (
        override
        if override is not None
        else os.environ.get("PO_BACKEND", "")
    ).strip().lower()

    if have_tmux is None:
        have_tmux = shutil.which("tmux") is not None
    if is_tty is None:
        is_tty = _stdout_is_tty()

    if choice == "stub":
        return StubBackend
    if choice == "cli":
        return ClaudeCliBackend
    if choice == "tmux":
        if not have_tmux:
            raise RuntimeError(
                "PO_BACKEND=tmux but `tmux` is not on PATH"
            )
        return TmuxClaudeBackend

    # auto / unset: tmux only when both available AND interactive.
    if have_tmux and is_tty:
        return TmuxClaudeBackend
    return ClaudeCliBackend
