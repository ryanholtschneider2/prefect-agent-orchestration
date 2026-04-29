"""Unit tests for `agent_step` — the simplified one-turn primitive.

These tests stub out `bd` shellouts and the agent backend so we exercise
the orchestration logic (resumability, bead-stamping, convergence ladder)
without spawning real Claude or `bd` processes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import prefect_orchestration.agent_step as agent_step_mod
from prefect_orchestration.agent_step import agent_step


def _write_prompt(agents_dir: Path, role: str, body: str = "You are {{seed_id}}.") -> Path:
    role_dir = agents_dir / role
    role_dir.mkdir(parents=True, exist_ok=True)
    (role_dir / "prompt.md").write_text(body)
    return role_dir


class _FakeSession:
    """Minimal AgentSession-like stub. Records prompts; returns a canned reply."""

    def __init__(self, replies: list[str] | None = None,
                 session_id: str | None = None):
        self.prompts: list[str] = []
        self._replies = list(replies or ["[stub] ack"])
        # Mimic AgentSession.session_id (None → fresh; str → resumed)
        self.session_id = session_id

    def prompt(self, text: str, **_kw: Any) -> str:
        self.prompts.append(text)
        return self._replies.pop(0) if self._replies else "[stub] ack"


@pytest.fixture
def fake_bd(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """In-memory bd substitute. Returns `state` so tests can manipulate it."""
    state: dict[str, dict[str, Any]] = {}

    def fake_bd_show(bead_id: str, rig_path: Any = None) -> dict | None:
        return state.get(bead_id)

    def fake_create_child_bead(parent, child_id, **_kw):
        if child_id not in state:
            state[child_id] = {
                "id": child_id, "status": "open", "title": _kw.get("title", ""),
                "metadata": {}, "closure_reason": "", "notes": "",
            }
        return child_id

    def fake_close_issue(bead_id, notes=None, rig_path=None):
        if bead_id in state:
            state[bead_id]["status"] = "closed"
            state[bead_id]["closure_reason"] = notes or "force-closed"

    def fake_bd_available() -> bool:
        return True

    monkeypatch.setattr(agent_step_mod, "_bd_show", fake_bd_show)
    monkeypatch.setattr(agent_step_mod, "create_child_bead", fake_create_child_bead)
    monkeypatch.setattr(agent_step_mod, "close_issue", fake_close_issue)
    monkeypatch.setattr(agent_step_mod, "_bd_available", fake_bd_available)
    # Don't actually shell out for `bd update --description`.
    monkeypatch.setattr(agent_step_mod.subprocess, "run", lambda *a, **kw: None)
    return state


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    """Patch `_build_session` to return a recorder we can inspect."""
    sess = _FakeSession()
    monkeypatch.setattr(agent_step_mod, "_build_session", lambda **_kw: sess)
    return sess


# ─── core happy path ────────────────────────────────────────────────


def test_agent_step_seed_only_agent_closes(
    tmp_path: Path, fake_bd: dict, fake_session: _FakeSession
) -> None:
    """Agent runs against the seed, closes the bead → return verdict 'complete'."""
    _write_prompt(tmp_path / "agents", "summarizer")
    fake_bd["bead-1"] = {
        "id": "bead-1", "status": "open", "title": "x",
        "metadata": {}, "closure_reason": "", "notes": "",
    }

    # Simulate the agent closing the bead during prompt().
    original = fake_session.prompt

    def closing_prompt(text: str, **kw: Any) -> str:
        fake_bd["bead-1"]["status"] = "closed"
        fake_bd["bead-1"]["closure_reason"] = "complete"
        return original(text, **kw)

    fake_session.prompt = closing_prompt  # type: ignore[assignment]

    result = agent_step(
        agent_dir=tmp_path / "agents" / "summarizer",
        task="Summarize this.",
        seed_id="bead-1",
        rig_path=str(tmp_path),
    )
    assert result.bead_id == "bead-1"
    assert result.closed_by == "agent"
    assert result.verdict == "complete"
    assert not result.from_cache


def test_agent_step_iter_creates_child_bead(
    tmp_path: Path, fake_bd: dict, fake_session: _FakeSession
) -> None:
    """`iter_n=1` + `step='plan'` → operates on `<seed>.plan.iter1`."""
    _write_prompt(tmp_path / "agents", "planner")
    fake_bd["seed"] = {"id": "seed", "status": "open", "title": "s",
                       "metadata": {}, "closure_reason": "", "notes": ""}

    # Agent closes the iter bead with a verdict keyword.
    original = fake_session.prompt

    def closing_prompt(text: str, **kw: Any) -> str:
        bid = "seed.plan.iter1"
        fake_bd[bid]["status"] = "closed"
        fake_bd[bid]["closure_reason"] = "approved: looks good"
        return original(text, **kw)

    fake_session.prompt = closing_prompt  # type: ignore[assignment]

    result = agent_step(
        agent_dir=tmp_path / "agents" / "planner",
        task="Plan task.",
        seed_id="seed",
        rig_path=str(tmp_path),
        iter_n=1,
        step="plan",
        verdict_keywords=("approved", "rejected"),
    )
    assert result.bead_id == "seed.plan.iter1"
    assert result.verdict == "approved"
    assert "looks good" in result.summary


# ─── resumability ───────────────────────────────────────────────────


def test_agent_step_skips_when_bead_already_closed(
    tmp_path: Path, fake_bd: dict, fake_session: _FakeSession
) -> None:
    """Already-closed bead → return verdict from cache, don't run agent."""
    _write_prompt(tmp_path / "agents", "x")
    fake_bd["bead-c"] = {
        "id": "bead-c", "status": "closed", "title": "x",
        "metadata": {}, "closure_reason": "approved: prior run", "notes": "",
    }
    result = agent_step(
        agent_dir=tmp_path / "agents" / "x",
        task="ignored",
        seed_id="bead-c",
        rig_path=str(tmp_path),
        verdict_keywords=("approved", "rejected"),
    )
    assert result.from_cache
    assert result.closed_by == "cache"
    assert result.verdict == "approved"
    assert fake_session.prompts == []  # NO turn happened


# ─── convergence ladder: nudge ──────────────────────────────────────


def test_agent_step_nudges_when_agent_forgot_to_close(
    tmp_path: Path, fake_bd: dict, fake_session: _FakeSession
) -> None:
    """Agent didn't close → nudge turn fires → agent closes on nudge."""
    _write_prompt(tmp_path / "agents", "x")
    fake_bd["bead-n"] = {
        "id": "bead-n", "status": "open", "title": "x",
        "metadata": {}, "closure_reason": "", "notes": "",
    }
    # First prompt: agent forgets to close. Second prompt (nudge): agent closes.
    call_count = {"n": 0}

    def stubbed_prompt(text: str, **_kw: Any) -> str:
        call_count["n"] += 1
        if call_count["n"] == 2:  # nudge turn
            fake_bd["bead-n"]["status"] = "closed"
            fake_bd["bead-n"]["closure_reason"] = "complete (after nudge)"
        return f"reply {call_count['n']}"

    fake_session.prompt = stubbed_prompt  # type: ignore[assignment]

    result = agent_step(
        agent_dir=tmp_path / "agents" / "x",
        task="t",
        seed_id="bead-n",
        rig_path=str(tmp_path),
    )
    assert call_count["n"] == 2
    assert result.closed_by == "nudge"
    assert "after nudge" in result.summary


# ─── convergence ladder: force-close ────────────────────────────────


def test_agent_step_force_closes_when_nudge_fails(
    tmp_path: Path, fake_bd: dict, fake_session: _FakeSession
) -> None:
    """Bead still open after nudge → force-close with failure-coded reason."""
    _write_prompt(tmp_path / "agents", "x")
    fake_bd["bead-f"] = {
        "id": "bead-f", "status": "open", "title": "x",
        "metadata": {}, "closure_reason": "", "notes": "",
    }

    # Both turns leave the bead open; orchestrator force-closes.
    fake_session.prompt = lambda text, **_kw: "ack"  # type: ignore[assignment]

    result = agent_step(
        agent_dir=tmp_path / "agents" / "x",
        task="t",
        seed_id="bead-f",
        rig_path=str(tmp_path),
    )
    assert result.closed_by == "force"
    assert result.verdict == "failed"
    # Defensive close ran:
    assert fake_bd["bead-f"]["status"] == "closed"
    assert "nudge failed" in fake_bd["bead-f"]["closure_reason"]


# ─── resumed-session optimisation ────────────────────────────────────


def test_agent_step_uses_short_prompt_on_resumed_session(
    tmp_path: Path, fake_bd: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When sess.session_id is set (--resume <uuid>), agent_step sends
    only the task-pointer prompt, not the full identity prompt.md.

    Saves token cost on iter2+ calls (the agent already has the
    identity from turn 1's conversation).
    """
    _write_prompt(
        tmp_path / "agents", "x",
        body="You are X — full identity preamble that should NOT be re-sent.",
    )
    fake_bd["bead-r"] = {
        "id": "bead-r", "status": "open", "title": "x",
        "metadata": {}, "closure_reason": "", "notes": "",
    }
    sess = _FakeSession(session_id="prior-uuid-from-turn-1")

    def closing_prompt(text: str, **kw: Any) -> str:
        sess.prompts.append(text)
        fake_bd["bead-r"]["status"] = "closed"
        fake_bd["bead-r"]["closure_reason"] = "complete"
        return "ack"

    sess.prompt = closing_prompt  # type: ignore[assignment]
    monkeypatch.setattr(agent_step_mod, "_build_session", lambda **_kw: sess)

    agent_step(
        agent_dir=tmp_path / "agents" / "x",
        task="some task",
        seed_id="bead-r",
        rig_path=str(tmp_path),
    )

    assert len(sess.prompts) == 1
    sent = sess.prompts[0]
    # Resumed prompt mentions the bead pointer + close-contract reference,
    # but NOT the full identity preamble:
    assert "bead-r" in sent
    assert "bd show" in sent
    assert "full identity preamble" not in sent
    # Should be much shorter than the full identity prompt (which is
    # ~15 lines / 600+ chars in real agent prompt files).
    assert len(sent) < 500
