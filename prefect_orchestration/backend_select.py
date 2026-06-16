"""Default agent-runtime backend picker.

Single seam used by packs and ad-hoc scripts to choose between
Claude, Codex, Cursor, and stub variants. The pack-side default in
`software_dev.py` already does `shutil.which("tmux")`; this
helper hardens it by also requiring stdout to be a TTY, which matters
inside containers where tmux is installed but no terminal is attached.

Honors explicit Claude, Codex, Cursor, and stub `PO_BACKEND` values.
Tmux backends without a tmux binary on PATH raise rather than falling back.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from typing import Literal, Type

from prefect_orchestration.agent_session import (
    ClaudeCliBackend,
    CodexCliBackend,
    CursorCliBackend,
    SessionBackend,
    StubBackend,
    TmuxClaudeBackend,
    TmuxCodexBackend,
    TmuxCursorBackend,
)

BackendChoice = Literal[
    "cli",
    "tmux",
    "stub",
    "auto",
    "codex-cli",
    "codex-tmux",
    "codex-tmux-stream",
    "cursor-cli",
    "cursor-tmux",
]


def _stdout_is_tty() -> bool:
    """Return True iff stdout is attached to a TTY (`isatty()`)."""
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def adapt_backend_to_start_command(
    backend: Type[SessionBackend],
    start_command: str | None,
) -> Type[SessionBackend]:
    """Swap backend families to match `start_command`.

    `agent_step._build_session()` historically picked the backend class
    first, then passed any per-role `start_command` into that backend's
    constructor. That breaks when the default backend is Claude-flavored
    but the runtime override is `codex exec ...`: the Claude backend
    still appends Claude-only flags like `--print`.

    This helper keeps the transport shape stable (tmux stays tmux, cli
    stays cli) while switching between the Claude and Codex backend
    families based on the actual executable being invoked.
    """
    if not start_command:
        return backend
    try:
        argv = shlex.split(start_command)
    except ValueError:
        return backend
    if not argv:
        return backend

    executable = os.path.basename(argv[0]).lower()
    if executable == "codex":
        if backend is TmuxClaudeBackend:
            return TmuxCodexBackend
        if backend is ClaudeCliBackend:
            return CodexCliBackend
    if executable in {"cursor-agent", "agent"}:
        if backend in {TmuxClaudeBackend, TmuxCodexBackend}:
            return TmuxCursorBackend
        if backend in {ClaudeCliBackend, CodexCliBackend}:
            return CursorCliBackend
    if executable == "claude":
        if backend is TmuxCodexBackend:
            return TmuxClaudeBackend
        if backend is CodexCliBackend:
            return ClaudeCliBackend
    return backend


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
        (override if override is not None else os.environ.get("PO_BACKEND", ""))
        .strip()
        .lower()
    )

    if have_tmux is None:
        have_tmux = shutil.which("tmux") is not None
    if is_tty is None:
        is_tty = _stdout_is_tty()

    if choice == "stub":
        return StubBackend
    if choice == "cli":
        return ClaudeCliBackend
    if choice == "codex-cli":
        return CodexCliBackend
    if choice == "cursor-cli":
        return CursorCliBackend
    if choice == "tmux":
        if not have_tmux:
            raise RuntimeError("PO_BACKEND=tmux but `tmux` is not on PATH")
        return TmuxClaudeBackend
    if choice in {"codex-tmux", "codex-tmux-stream"}:
        if not have_tmux:
            raise RuntimeError(f"PO_BACKEND={choice} but `tmux` is not on PATH")
        return TmuxCodexBackend
    if choice == "cursor-tmux":
        if not have_tmux:
            raise RuntimeError("PO_BACKEND=cursor-tmux but `tmux` is not on PATH")
        return TmuxCursorBackend

    # auto / unset: tmux only when both available AND interactive.
    if have_tmux and is_tty:
        return TmuxClaudeBackend
    return ClaudeCliBackend
