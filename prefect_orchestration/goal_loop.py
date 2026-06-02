"""po formula: `goal-loop` — actor/critic loop against a goal.

An **actor** works toward a goal; after each turn a **critic** judges the work
against the goal and either approves it (done), rejects it with specific
feedback (the actor tries again), or declares it infeasible. Unlike the Ralph
loop (keep iterating open-endedly to make something better), this terminates on
an explicit acceptance contract.

Four terminal states:

* ``success``          — critic approved: the goal is met.
* ``abandoned-actor``  — the actor closed ``unable:``: it can't accomplish the goal.
* ``abandoned-critic`` — the critic closed ``infeasible:``: no further turns will help.
* ``exhausted``        — ``max_iters`` reached without approval.

Drive it with an inline ``goal`` (a fresh per-run bead is minted so recurring
schedules aren't short-circuited by the closed-bead cache) or against an
existing bead whose description is the goal. ``agent`` overrides the actor role
(default ``goal-actor``); ``critic`` overrides the critic role (default
``goal-critic``). Built on the same `agent_step` + iter-bead + verdict machinery
the software-dev pipeline uses.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from prefect import flow, get_run_logger

from prefect_orchestration.agent_step import agent_step
from prefect_orchestration.beads_meta import mint_seed_bead
from prefect_orchestration.formulas import discover_agent_dir

DEFAULT_ACTOR = "goal-actor"
DEFAULT_CRITIC = "goal-critic"
DEFAULT_MAX_ITERS = 5


def _bead_description(issue_id: str, rig_path: str) -> str | None:
    """The bead's description field (the goal, when no inline goal is passed)."""
    if shutil.which("bd") is None:
        return None
    proc = subprocess.run(
        ["bd", "show", issue_id, "--json"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(rig_path),
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
        row = data[0] if isinstance(data, list) and data else data
        desc = row.get("description")
        return desc if isinstance(desc, str) and desc.strip() else None
    except Exception:  # noqa: BLE001
        return None


def _resolve_goal(issue_id: str, goal: str | None, rig_path: str) -> str:
    """The goal text: explicit `goal` arg wins, else the bead's description."""
    if goal:
        return goal
    desc = _bead_description(issue_id, rig_path)
    if desc:
        return desc
    raise ValueError(
        "goal-loop: no goal. Pass --goal=<text> or run against a bead whose "
        "description is the goal."
    )


@flow(name="goal-loop", flow_run_name="{issue_id}", log_prints=True)
def goal_loop(
    issue_id: str,
    rig: str,
    rig_path: str,
    goal: str | None = None,
    agent: str | None = None,
    critic: str | None = None,
    max_iters: int = DEFAULT_MAX_ITERS,
    parent_bead: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the actor/critic goal loop. See module docstring for the contract."""
    logger = get_run_logger()
    goal_text = _resolve_goal(issue_id, goal, rig_path)
    actor_role = agent or DEFAULT_ACTOR
    critic_role = critic or DEFAULT_CRITIC
    actor_dir = discover_agent_dir(actor_role)
    critic_dir = discover_agent_dir(critic_role)

    # A fresh per-run seed when given an inline goal (so recurring schedules
    # don't reuse a closed bead); else operate on the supplied bead. dry_run
    # skips minting and runs the stub against issue_id.
    if goal and not dry_run:
        seed_id = mint_seed_bead(
            issue_id, goal_text, rig_path=rig_path, label="po-goal-loop"
        )
    else:
        seed_id = issue_id

    logger.info(
        "goal-loop: seed=%s actor=%s critic=%s max_iters=%d",
        seed_id,
        actor_role,
        critic_role,
        max_iters,
    )

    last_feedback: str | None = None
    for i in range(1, max_iters + 1):
        # Actor turn — task is the goal (iter 1) or the goal + critic feedback.
        if last_feedback:
            actor_task = (
                f"## Goal\n\n{goal_text}\n\n"
                f"## The reviewer rejected your previous attempt\n\n{last_feedback}\n\n"
                "Address that feedback, then close per your contract."
            )
        else:
            actor_task = f"## Goal\n\n{goal_text}\n\nWork toward this, then close per your contract."
        actor_res = agent_step(
            agent_dir=actor_dir,
            task=actor_task,
            seed_id=seed_id,
            rig_path=rig_path,
            step="actor",
            iter_n=i,
            verdict_keywords=("done", "unable"),
            dry_run=dry_run,
        )
        logger.info("goal-loop iter %d: actor verdict=%r", i, actor_res.verdict)
        if actor_res.verdict == "unable":
            return _result("abandoned-actor", i, seed_id, actor_res.summary, max_iters)

        # Critic turn — judge the actor's work against the goal.
        critic_task = (
            f"## Goal\n\n{goal_text}\n\n"
            f"The actor reported it is done (its work bead is `{actor_res.bead_id}`). "
            f"Inspect what was actually done in {rig_path} and decide whether the goal "
            "is met. Close with approved / rejected: <feedback> / infeasible: <why>."
        )
        critic_res = agent_step(
            agent_dir=critic_dir,
            task=critic_task,
            seed_id=seed_id,
            rig_path=rig_path,
            step="critic",
            iter_n=i,
            verdict_keywords=("approved", "rejected", "infeasible"),
            dry_run=dry_run,
        )
        logger.info("goal-loop iter %d: critic verdict=%r", i, critic_res.verdict)
        if critic_res.verdict == "approved":
            return _result("success", i, seed_id, critic_res.summary, max_iters)
        if critic_res.verdict == "infeasible":
            return _result(
                "abandoned-critic", i, seed_id, critic_res.summary, max_iters
            )
        # rejected (or unparsed) → feed the critic's reason into the next actor turn.
        last_feedback = (
            critic_res.summary
            or "(no specific feedback; keep improving toward the goal)"
        )

    return _result("exhausted", max_iters, seed_id, last_feedback, max_iters)


def _result(
    status: str, iters: int, seed_id: str, detail: str | None, max_iters: int
) -> dict[str, Any]:
    return {
        "status": status,
        "iters": iters,
        "max_iters": max_iters,
        "seed_id": seed_id,
        "detail": detail or "",
    }


__all__ = ["goal_loop"]
