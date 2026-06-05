"""goal-loop formula — actor/critic loop terminal states + feedback threading."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import prefect_orchestration.goal_loop as gl
from prefect_orchestration.goal_loop import discover_agent_dir, goal_loop


def _make_agent_step(script: dict[tuple[str, int], tuple[str, str]]):
    """Fake agent_step returning scripted (verdict, summary) per (step, iter_n)."""
    calls: list[dict] = []

    def fake(**kw):
        calls.append(kw)
        verdict, summary = script.get((kw["step"], kw["iter_n"]), ("", ""))
        return SimpleNamespace(
            bead_id=f"{kw['seed_id']}-{kw['step']}-iter{kw['iter_n']}",
            verdict=verdict,
            summary=summary,
            from_cache=False,
            closed_by="agent",
        )

    return fake, calls


def _patch(monkeypatch, fake) -> None:
    monkeypatch.setattr(gl, "agent_step", fake)
    monkeypatch.setattr(gl, "mint_seed_bead", lambda *a, **k: "seed-1")


def test_goal_actor_and_critic_agents_ship() -> None:
    assert (discover_agent_dir("goal-actor") / "prompt.md").is_file()
    assert (discover_agent_dir("goal-critic") / "prompt.md").is_file()


def test_success_when_critic_approves(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = _make_agent_step(
        {
            ("actor", 1): ("done", "made it"),
            ("critic", 1): ("approved", "meets the goal"),
        }
    )
    _patch(monkeypatch, fake)
    out = goal_loop(issue_id="x", rig="r", rig_path="/tmp", goal="do it")
    assert out["status"] == "success"
    assert out["iters"] == 1


def test_abandoned_actor_when_actor_unable(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = _make_agent_step({("actor", 1): ("unable", "blocked on creds")})
    _patch(monkeypatch, fake)
    out = goal_loop(issue_id="x", rig="r", rig_path="/tmp", goal="do it")
    assert out["status"] == "abandoned-actor"
    assert out["detail"] == "blocked on creds"


def test_abandoned_critic_when_infeasible(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = _make_agent_step(
        {
            ("actor", 1): ("done", ""),
            ("critic", 1): ("infeasible", "contradictory goal"),
        }
    )
    _patch(monkeypatch, fake)
    out = goal_loop(issue_id="x", rig="r", rig_path="/tmp", goal="do it")
    assert out["status"] == "abandoned-critic"
    assert out["detail"] == "contradictory goal"


def test_exhausted_and_feedback_threaded_to_next_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, calls = _make_agent_step(
        {
            ("actor", 1): ("done", ""),
            ("critic", 1): ("rejected", "add a newline at EOF"),
            ("actor", 2): ("done", ""),
            ("critic", 2): ("rejected", "still missing"),
        }
    )
    _patch(monkeypatch, fake)
    out = goal_loop(issue_id="x", rig="r", rig_path="/tmp", goal="do it", max_iters=2)
    assert out["status"] == "exhausted"
    assert out["iters"] == 2
    # iter-2 actor task carries iter-1 critic feedback verbatim
    actor2 = next(c for c in calls if c["step"] == "actor" and c["iter_n"] == 2)
    assert "add a newline at EOF" in actor2["task"]


def test_inline_goal_mints_fresh_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, calls = _make_agent_step({("actor", 1): ("unable", "x")})
    monkeypatch.setattr(gl, "agent_step", fake)
    monkeypatch.setattr(gl, "mint_seed_bead", lambda issue_id, goal, **k: "minted-9")
    goal_loop(issue_id="x", rig="r", rig_path="/tmp", goal="do it")
    assert calls[0]["seed_id"] == "minted-9"


def test_dry_run_skips_mint_and_uses_issue_id(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, calls = _make_agent_step({("actor", 1): ("unable", "x")})
    monkeypatch.setattr(gl, "agent_step", fake)
    monkeypatch.setattr(
        gl, "mint_seed_bead", lambda *a, **k: pytest.fail("dry_run must not mint")
    )
    goal_loop(issue_id="x", rig="r", rig_path="/tmp", goal="do it", dry_run=True)
    assert calls[0]["seed_id"] == "x"


def test_resolve_goal_falls_back_to_bead_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, calls = _make_agent_step({("actor", 1): ("unable", "x")})
    monkeypatch.setattr(gl, "agent_step", fake)
    monkeypatch.setattr(
        gl, "_bead_description", lambda issue_id, rig_path: "the goal from the bead"
    )
    # no inline goal -> reads the bead description, no minting (operate on issue_id)
    goal_loop(issue_id="real-bead", rig="r", rig_path="/tmp")
    assert calls[0]["seed_id"] == "real-bead"
    assert "the goal from the bead" in calls[0]["task"]
