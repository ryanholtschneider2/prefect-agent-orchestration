"""`greeter-agent` — runs the `greeter` operating agent via AgentSession.

Scaffolded by `po new agent`. This @flow is the trigger surface: register a
cron/interval/event deployment for it (see po_formulas deployments / `po run
greeter-agent --at ...`). Each run renders `agents/greeter/prompt.md` and takes one
agent turn. Replace the body with the real loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from prefect import flow

from prefect_orchestration.agent_session import AgentSession
from prefect_orchestration.backend_select import select_default_backend
from prefect_orchestration.templates import render_template

_AGENTS_DIR = Path(__file__).parent / "agents"


def _make_backend(role: str, issue: str):
    """Instantiate the selected backend factory (tmux needs issue+role)."""
    factory = select_default_backend()
    try:
        return factory(issue=issue, role=role)
    except TypeError:
        return factory()


@flow(name="greeter-agent", flow_run_name="greeter", log_prints=True)
def greeter_agent(
    rig_path: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run one turn of the `greeter` agent against `rig_path`."""
    rig = Path(rig_path).expanduser().resolve()
    prompt = render_template(_AGENTS_DIR, "greeter", rig_path=rig)

    if dry_run:
        return {"status": "dry-run", "agent": "greeter", "prompt_chars": len(prompt)}

    session = AgentSession(
        role="greeter",
        repo_path=rig,
        backend=_make_backend("greeter", "greeter-agent"),
    )
    reply = session.prompt(prompt)
    return {"status": "ok", "agent": "greeter", "reply_chars": len(reply)}
