"""Prefect-aware transport helpers for ``po wait``.

Beads remains the source of truth for successful completion.  Prefect is
consulted only to notice terminal transport failures that would otherwise
leave a PO-owned bead claim waiting forever.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prefect_orchestration import status
from prefect_orchestration.beads_meta import BD_SHELL_TIMEOUT_S, _resolve_binary

FAILED_FLOW_STATES = frozenset({"FAILED", "CRASHED", "CANCELLED"})
COMMENT_PREFIX = "po-wait-terminal:"


@dataclass(frozen=True)
class TerminalFlow:
    """The actionable terminal details needed by ``po wait``."""

    flow_run_id: str
    state: str
    message: str


def _state_message(flow_run: Any) -> str:
    state = getattr(flow_run, "state", None)
    message = getattr(state, "message", None)
    if message:
        return str(message)
    message = getattr(flow_run, "state_message", None)
    return str(message) if message else "no Prefect state message"


def latest_terminal_flow(issue_id: str) -> TerminalFlow | None:
    """Return the latest failed PO flow for an issue, best-effort.

    An unreachable Prefect API is treated as "no observation" so the command
    retains its historical beads-only behavior during a control-plane outage.
    """
    try:
        import anyio
        from prefect.client.orchestration import get_client

        async def _read() -> TerminalFlow | None:
            async with get_client() as client:
                runs = await status.find_runs_by_issue_id(
                    client,
                    issue_id=issue_id,
                    limit=1,
                )
            if not runs:
                return None
            latest = runs[0]
            state = str(getattr(latest, "state_name", "") or "").upper()
            if state not in FAILED_FLOW_STATES:
                return None
            return TerminalFlow(
                flow_run_id=str(getattr(latest, "id", "unknown")),
                state=state.title(),
                message=_state_message(latest),
            )

        return anyio.run(_read)
    except Exception:  # noqa: BLE001 - Prefect observation must be best-effort
        return None


def _has_reconciliation_comment(row: dict, marker: str) -> bool:
    for comment in row.get("comments") or ():
        text = comment.get("text", "") if isinstance(comment, dict) else str(comment)
        if marker in text:
            return True
    return False


def reconcile_failed_claim(
    issue_id: str,
    row: dict,
    terminal: TerminalFlow,
    *,
    rig_path: Path | None,
) -> bool:
    """Annotate a failed run and safely release its exact PO-owned claim.

    Returns whether the assignee was cleared.  The bead status is deliberately
    untouched: a transport failure is not a quality verdict and cannot close
    or reopen work.
    """
    binary = _resolve_binary(rig_path)
    if binary is None:
        return False

    assignee = str(row.get("assignee") or "")
    expected_assignee = f"po-{terminal.flow_run_id[:8]}"
    clear_assignee = (
        row.get("status") == "in_progress" and assignee == expected_assignee
    )
    marker = f"{COMMENT_PREFIX}{terminal.flow_run_id}"
    message = (
        f"{marker} Prefect flow reached {terminal.state}: {terminal.message}. "
        "The bead was not closed and no success verdict was created. "
        f"PO-owned assignee cleared: {'yes' if clear_assignee else 'no'}."
    )
    cwd = str(rig_path) if rig_path is not None else None

    if not _has_reconciliation_comment(row, marker):
        try:
            subprocess.run(
                [binary, "comments", "add", issue_id, message],
                capture_output=True,
                text=True,
                check=False,
                cwd=cwd,
                timeout=BD_SHELL_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            pass

    if clear_assignee:
        try:
            proc = subprocess.run(
                [binary, "update", issue_id, "--assignee", ""],
                capture_output=True,
                text=True,
                check=False,
                cwd=cwd,
                timeout=BD_SHELL_TIMEOUT_S,
            )
            return proc.returncode == 0
        except subprocess.TimeoutExpired:
            return False
    return False
