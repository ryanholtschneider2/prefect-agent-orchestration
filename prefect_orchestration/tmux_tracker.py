"""In-process registry + cross-process scanner for tmux sessions PO spawns.

Two responsibilities:

1. Track every tmux session/window the current `po` process spawns so a
   SIGINT/SIGTERM handler can drain them on exit. Without this, killing
   `po run` leaves the detached tmux sessions (and their claude children +
   `sleep infinity` keep-alive panes) eating tmux slots and a Claude
   rate-limit slot indefinitely (issue prefect-orchestration-sav.3).

2. From a fresh process (e.g. `po retry`), find tmux artifacts left over
   from a prior crashed run for a specific issue id and kill them. The
   in-process registry is empty in this case — we scan `tmux list-sessions`
   and `tmux list-windows` instead.

Naming convention this module relies on (see attach.py + agent_session.py
`_scoped_names`):

* Unscoped layout: dedicated session per (issue, role), named
  ``po-{safe_issue}-{safe_role}``. Killing the session kills the role.
* Scoped layout: one shared session named ``po-{safe_scope}`` (where
  scope is ``rig`` or ``rig-epic``) with one window per role spawn named
  ``{safe_issue}-{safe_role}``. Killing the window kills just that role,
  leaving peer roles in the session intact.

`safe_*` means dots replaced with underscores (tmux treats `.` as a pane
separator).
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class TmuxRef:
    """Identity of one tmux artifact spawned by PO.

    `session_name` is always the tmux session name. `window_name` is the
    window inside that session for scoped runs, or None for the unscoped
    one-session-per-role layout. `target` is the opaque string passed to
    `tmux <cmd> -t` — same as what `_spawn_tmux` returned (a session name
    or `@<window_id>`).
    """

    session_name: str
    window_name: str | None
    target: str


_LOCK = threading.Lock()
_LIVE: set[TmuxRef] = set()


def register(ref: TmuxRef) -> None:
    """Record a freshly-spawned tmux artifact."""
    with _LOCK:
        _LIVE.add(ref)


def unregister_by_target(target: str) -> None:
    """Drop the entry whose `.target` matches (idempotent)."""
    with _LOCK:
        for ref in list(_LIVE):
            if ref.target == target:
                _LIVE.discard(ref)


def snapshot() -> list[TmuxRef]:
    """Return a stable copy of the live set (for tests / diagnostics)."""
    with _LOCK:
        return sorted(_LIVE, key=lambda r: (r.session_name, r.window_name or ""))


def _kill(ref: TmuxRef) -> bool:
    cmd = [
        "tmux",
        "kill-window" if ref.window_name else "kill-session",
        "-t",
        ref.target,
    ]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    return proc.returncode == 0


def kill_all() -> int:
    """Tear down every tracked artifact. Returns count successfully killed.

    Drains the registry even if individual `tmux kill` calls fail — the
    intent is "fire and forget on shutdown", not "halt on error".
    """
    with _LOCK:
        refs = list(_LIVE)
        _LIVE.clear()
    if shutil.which("tmux") is None:
        return 0
    n = 0
    for ref in refs:
        if _kill(ref):
            n += 1
    return n


def _safe(s: str) -> str:
    return s.replace(".", "_")


def _list_sessions() -> list[str]:
    proc = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.splitlines() if line]


def _list_windows(session: str) -> list[str]:
    proc = subprocess.run(
        ["tmux", "list-windows", "-t", session, "-F", "#{window_name}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.splitlines() if line]


def kill_for_issue(issue_id: str) -> int:
    """Kill any tmux artifacts that belong to `issue_id`.

    Used by `po retry` to clean up after a crashed prior run, when the
    in-process registry is empty (we're a fresh process). Scans the live
    tmux server and matches against the naming convention:

    * Unscoped: session named ``po-{safe_issue}-*`` → kill whole session.
    * Scoped: window named ``{safe_issue}-*`` inside any ``po-*`` session
      → kill just that window (peer roles for other issues survive).

    Returns the number of artifacts successfully killed. No-op when tmux
    is not on PATH.
    """
    if shutil.which("tmux") is None:
        return 0
    safe = _safe(issue_id)
    n = 0
    for session in _list_sessions():
        if session.startswith(f"po-{safe}-") or session == f"po-{safe}":
            proc = subprocess.run(
                ["tmux", "kill-session", "-t", session],
                capture_output=True,
                check=False,
            )
            if proc.returncode == 0:
                n += 1
            continue
        if not session.startswith("po-"):
            continue
        # Possibly a shared scope-session (`po-{rig}` or `po-{rig}-{epic}`)
        # holding windows for many issues. Kill only matching windows.
        for window in _list_windows(session):
            if window == safe or window.startswith(f"{safe}-"):
                proc = subprocess.run(
                    ["tmux", "kill-window", "-t", f"{session}:{window}"],
                    capture_output=True,
                    check=False,
                )
                if proc.returncode == 0:
                    n += 1
    return n
