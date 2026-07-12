"""Explicit destructive cancellation for one PO issue."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class CancelResult:
    flow_runs: int
    tmux_sessions: int


def _kill_issue_tmux(issue_id: str) -> int:
    safe = issue_id.replace(".", "_")
    prefix = f"po-{safe}-"
    listed = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if listed.returncode != 0:
        return 0
    names = [name for name in listed.stdout.splitlines() if name.startswith(prefix)]
    for name in names:
        subprocess.run(["tmux", "kill-session", "-t", name], check=False)
    return len(names)


def cancel_issue(issue_id: str) -> CancelResult:
    import anyio
    from prefect.client.orchestration import get_client
    from prefect.states import Cancelled

    from prefect_orchestration.status import find_runs_by_issue_id

    async def cancel_runs() -> int:
        async with get_client() as client:
            runs = await find_runs_by_issue_id(
                client, issue_id=issue_id, state="Running", limit=100
            )
            for run in runs:
                await client.set_flow_run_state(
                    run.id,
                    Cancelled(message=f"Explicit po cancel {issue_id}"),
                    force=True,
                )
            return len(runs)

    return CancelResult(
        flow_runs=anyio.run(cancel_runs), tmux_sessions=_kill_issue_tmux(issue_id)
    )
