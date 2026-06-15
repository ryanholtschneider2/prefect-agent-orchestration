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
from prefect_orchestration import iter_bead_ids
from prefect_orchestration.agent_session import (
    RateLimitError,
    StepTimeoutError,
    TmuxClaudeBackend,
    TmuxCodexBackend,
)
from prefect_orchestration.agent_step import agent_step
from prefect_orchestration.role_config import RoleRuntime


def _write_prompt(
    agents_dir: Path, role: str, body: str = "You are {{seed_id}}."
) -> Path:
    role_dir = agents_dir / role
    role_dir.mkdir(parents=True, exist_ok=True)
    (role_dir / "prompt.md").write_text(body)
    return role_dir


class _FakeSession:
    """Minimal AgentSession-like stub. Records prompts; returns a canned reply."""

    def __init__(self, replies: list[str] | None = None, session_id: str | None = None):
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
                "id": child_id,
                "status": "open",
                "title": _kw.get("title", ""),
                "metadata": {},
                "closure_reason": "",
                "notes": "",
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
        "id": "bead-1",
        "status": "open",
        "title": "x",
        "metadata": {},
        "closure_reason": "",
        "notes": "",
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
    """`iter_n=1` + `step='plan'` → operates on `<seed>-plan-iter1`."""
    _write_prompt(tmp_path / "agents", "planner")
    fake_bd["seed"] = {
        "id": "seed",
        "status": "open",
        "title": "s",
        "metadata": {},
        "closure_reason": "",
        "notes": "",
    }

    # Agent closes the iter bead with a verdict keyword.
    original = fake_session.prompt

    def closing_prompt(text: str, **kw: Any) -> str:
        bid = "seed-plan-iter1"
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
    assert result.bead_id == "seed-plan-iter1"
    assert result.verdict == "approved"
    assert "looks good" in result.summary


def test_agent_step_adopts_backend_assigned_iter_id(
    tmp_path: Path,
    fake_bd: dict,
    fake_session: _FakeSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a br rig, `create_child_bead` mints its own id (no `--id` flag).

    `agent_step` must adopt the returned id as canonical and thread it through
    the description stamp, the convergence-ladder status probes, and the verdict
    read — NOT the computed `<seed>-<step>-iter<N>`, which is a phantom bead on br.
    """
    _write_prompt(tmp_path / "agents", "planner")
    fake_bd["seed"] = {
        "id": "seed",
        "status": "open",
        "title": "s",
        "metadata": {},
        "closure_reason": "",
        "notes": "",
    }

    # Mimic br: ignore the requested `<seed>-plan-iter1` and return a fresh id.
    br_id = "po-7f3a9c"

    def br_create_child_bead(parent: str, child_id: str, **_kw: Any) -> str:
        assert child_id == "seed-plan-iter1"  # computed id is the requested one
        fake_bd[br_id] = {
            "id": br_id,
            "status": "open",
            "title": _kw.get("title", ""),
            "metadata": {},
            "closure_reason": "",
            "notes": "",
        }
        return br_id  # br-assigned id, differs from the requested id

    monkeypatch.setattr(agent_step_mod, "create_child_bead", br_create_child_bead)

    # Capture which bead the description stamp targets — must be the br id.
    stamped: list[str] = []
    monkeypatch.setattr(
        agent_step_mod,
        "_stamp_description",
        lambda bead_id, *_a, **_kw: stamped.append(bead_id),
    )

    # The agent closes the br-assigned bead (the phantom computed id stays absent).
    original = fake_session.prompt

    def closing_prompt(text: str, **kw: Any) -> str:
        fake_bd[br_id]["status"] = "closed"
        fake_bd[br_id]["closure_reason"] = "approved: br round-trip"
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

    assert result.bead_id == br_id  # operated on the returned id, not the computed
    assert result.closed_by == "agent"
    assert result.verdict == "approved"
    assert stamped == [br_id]  # description stamped on the real bead
    assert "seed-plan-iter1" not in fake_bd  # phantom id never materialized


def test_agent_step_br_records_iter_id_map(
    tmp_path: Path,
    fake_bd: dict,
    fake_session: _FakeSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On br, the first call records the convention→real-id mapping in the
    run-dir so later calls can resolve the real bead instead of re-minting."""
    _write_prompt(tmp_path / "agents", "builder")
    fake_bd["seed"] = {
        "id": "seed",
        "status": "open",
        "title": "s",
        "metadata": {},
        "closure_reason": "",
        "notes": "",
    }
    run_dir = tmp_path / "run"
    br_id = "br-real-1"

    def br_create_child_bead(parent: str, child_id: str, **_kw: Any) -> str:
        fake_bd[br_id] = {
            "id": br_id,
            "status": "open",
            "title": _kw.get("title", ""),
            "metadata": {},
            "closure_reason": "",
            "notes": "",
        }
        return br_id

    monkeypatch.setattr(agent_step_mod, "create_child_bead", br_create_child_bead)

    original = fake_session.prompt

    def closing_prompt(text: str, **kw: Any) -> str:
        fake_bd[br_id]["status"] = "closed"
        fake_bd[br_id]["closure_reason"] = "complete: built"
        return original(text, **kw)

    fake_session.prompt = closing_prompt  # type: ignore[assignment]

    result = agent_step(
        agent_dir=tmp_path / "agents" / "builder",
        task="Build it.",
        seed_id="seed",
        rig_path=str(tmp_path),
        run_dir=str(run_dir),
        iter_n=1,
        step="build",
        verdict_keywords=("complete",),
    )
    assert result.bead_id == br_id
    # The mapping persists under the convention key for the next call.
    assert iter_bead_ids.lookup(run_dir, "seed-build-iter1") == br_id


def test_agent_step_br_reentry_resolves_recorded_id_no_remint(
    tmp_path: Path,
    fake_bd: dict,
    fake_session: _FakeSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-entry for an already-completed br role-step must resolve the recorded
    real id, short-circuit on the cache, and NOT re-mint a fresh bead — this is
    the fix for the phantom-id re-dispatch loop (prefect-orchestration-99k)."""
    _write_prompt(tmp_path / "agents", "builder")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # Prior call already recorded the mapping and the real bead is closed.
    iter_bead_ids.record(run_dir, "seed-build-iter1", "br-real-1")
    fake_bd["br-real-1"] = {
        "id": "br-real-1",
        "status": "closed",
        "title": "build iter 1 for seed",
        "metadata": {},
        "closure_reason": "complete: built earlier",
        "notes": "",
    }

    create_calls = {"n": 0}

    def exploding_create(parent: str, child_id: str, **_kw: Any) -> str:
        create_calls["n"] += 1
        return "br-real-2"  # a fresh mint — must NOT happen on re-entry

    monkeypatch.setattr(agent_step_mod, "create_child_bead", exploding_create)

    result = agent_step(
        agent_dir=tmp_path / "agents" / "builder",
        task="Build it.",
        seed_id="seed",
        rig_path=str(tmp_path),
        run_dir=str(run_dir),
        iter_n=1,
        step="build",
        verdict_keywords=("complete",),
    )
    assert result.from_cache is True
    assert result.bead_id == "br-real-1"
    assert result.verdict == "complete"
    assert create_calls["n"] == 0  # no re-mint, no phantom re-dispatch
    # No agent turn was run on the cache short-circuit.
    assert fake_session.prompts == []


# ─── resumability ───────────────────────────────────────────────────


def test_agent_step_skips_when_bead_already_closed(
    tmp_path: Path, fake_bd: dict, fake_session: _FakeSession
) -> None:
    """Already-closed bead → return verdict from cache, don't run agent."""
    _write_prompt(tmp_path / "agents", "x")
    fake_bd["bead-c"] = {
        "id": "bead-c",
        "status": "closed",
        "title": "x",
        "metadata": {},
        "closure_reason": "approved: prior run",
        "notes": "",
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
        "id": "bead-n",
        "status": "open",
        "title": "x",
        "metadata": {},
        "closure_reason": "",
        "notes": "",
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
        "id": "bead-f",
        "status": "open",
        "title": "x",
        "metadata": {},
        "closure_reason": "",
        "notes": "",
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
        tmp_path / "agents",
        "x",
        body="You are X — full identity preamble that should NOT be re-sent.",
    )
    fake_bd["bead-r"] = {
        "id": "bead-r",
        "status": "open",
        "title": "x",
        "metadata": {},
        "closure_reason": "",
        "notes": "",
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


def test_agent_step_rotates_oauth_slot_and_retries_after_rate_limit(
    tmp_path: Path, fake_bd: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_prompt(tmp_path / "agents", "builder")
    fake_bd["bead-rl"] = {
        "id": "bead-rl",
        "status": "open",
        "title": "x",
        "metadata": {},
        "closure_reason": "",
        "notes": "",
    }

    first = _FakeSession()
    second = _FakeSession()

    def first_prompt(text: str, **_kw: Any) -> str:
        first.prompts.append(text)
        raise RateLimitError(reset_time="1:30am (America/New_York)")

    def second_prompt(text: str, **_kw: Any) -> str:
        second.prompts.append(text)
        fake_bd["bead-rl"]["status"] = "closed"
        fake_bd["bead-rl"]["closure_reason"] = "complete"
        return "ack"

    first.prompt = first_prompt  # type: ignore[assignment]
    second.prompt = second_prompt  # type: ignore[assignment]
    sessions = iter([first, second])
    monkeypatch.setattr(agent_step_mod, "_build_session", lambda **_kw: next(sessions))
    monkeypatch.setattr(agent_step_mod, "oauth_failover_budget", lambda: 1)
    rotated: list[int] = []
    monkeypatch.setattr(
        agent_step_mod,
        "rotate_to_next_oauth_pool_slot",
        lambda: rotated.append(1) or 1,
    )

    result = agent_step(
        agent_dir=tmp_path / "agents" / "builder",
        task="build it",
        seed_id="bead-rl",
        rig_path=str(tmp_path),
    )

    assert result.closed_by == "agent"
    assert rotated == [1]
    assert len(first.prompts) == 1
    assert len(second.prompts) == 1


def test_agent_step_rate_limit_without_failover_bubbles(
    tmp_path: Path, fake_bd: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_prompt(tmp_path / "agents", "builder")
    fake_bd["bead-rl2"] = {
        "id": "bead-rl2",
        "status": "open",
        "title": "x",
        "metadata": {},
        "closure_reason": "",
        "notes": "",
    }
    sess = _FakeSession()

    def limited_prompt(text: str, **_kw: Any) -> str:
        sess.prompts.append(text)
        raise RateLimitError(reset_time="2:00am")

    sess.prompt = limited_prompt  # type: ignore[assignment]
    monkeypatch.setattr(agent_step_mod, "_build_session", lambda **_kw: sess)
    monkeypatch.setattr(agent_step_mod, "oauth_failover_budget", lambda: 0)

    with pytest.raises(RateLimitError):
        agent_step(
            agent_dir=tmp_path / "agents" / "builder",
            task="build it",
            seed_id="bead-rl2",
            rig_path=str(tmp_path),
        )


def test_agent_step_step_timeout_propagates(
    tmp_path: Path, fake_bd: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """StepTimeoutError from session.prompt bubbles out without OAuth rotation
    or nudge — the bead stays open for the operator to see + `po retry`."""
    _write_prompt(tmp_path / "agents", "builder")
    fake_bd["bead-to"] = {
        "id": "bead-to",
        "status": "open",
        "title": "x",
        "metadata": {},
        "closure_reason": "",
        "notes": "",
    }
    sess = _FakeSession()

    def wedging_prompt(text: str, **_kw: Any) -> str:
        sess.prompts.append(text)
        raise StepTimeoutError(timeout_s=1800.0)

    sess.prompt = wedging_prompt  # type: ignore[assignment]
    monkeypatch.setattr(agent_step_mod, "_build_session", lambda **_kw: sess)
    rotated: list[int] = []
    monkeypatch.setattr(agent_step_mod, "oauth_failover_budget", lambda: 5)
    monkeypatch.setattr(
        agent_step_mod,
        "rotate_to_next_oauth_pool_slot",
        lambda: rotated.append(1) or 1,
    )

    with pytest.raises(StepTimeoutError) as exc:
        agent_step(
            agent_dir=tmp_path / "agents" / "builder",
            task="build it",
            seed_id="bead-to",
            rig_path=str(tmp_path),
        )

    assert exc.value.timeout_s == 1800.0
    assert rotated == []  # OAuth rotation only fires on RateLimitError
    assert len(sess.prompts) == 1  # no nudge — single turn, then bubble
    assert fake_bd["bead-to"]["status"] == "open"  # not force-closed


def test_build_session_switches_default_tmux_backend_to_codex_for_codex_start_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_dir = _write_prompt(tmp_path / "agents", "builder")

    monkeypatch.setattr(
        agent_step_mod,
        "select_default_backend",
        lambda: TmuxClaudeBackend,
    )
    monkeypatch.setattr(
        agent_step_mod,
        "resolve_role_runtime",
        lambda _agent_dir: RoleRuntime(
            start_command="codex exec --dangerously-bypass-approvals-and-sandbox"
        ),
    )

    sess = agent_step_mod._build_session(
        seed_id="pd-test",
        role="builder",
        rig_path=str(tmp_path),
        agent_dir=agent_dir,
        run_dir=tmp_path / ".planning" / "run",
        backend=None,
        dry_run=False,
    )

    assert isinstance(sess.backend, TmuxCodexBackend)
    assert sess.backend.start_command == (
        "codex exec --dangerously-bypass-approvals-and-sandbox"
    )


# ─── nanocorps-6q4: agent_step failure logging ──────────────────────


def test_agent_step_failure_writes_jsonl(
    tmp_path: Path, fake_bd: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing turn writes one parseable row to agent_step_failures.jsonl
    AND re-raises the original exception."""
    import json as _json

    _write_prompt(tmp_path / "agents", "builder")
    fake_bd["bead-fl1"] = {
        "id": "bead-fl1",
        "status": "open",
        "title": "x",
        "metadata": {},
        "closure_reason": "",
        "notes": "",
    }
    sess = _FakeSession()

    def boom(text: str, **_kw: Any) -> str:
        sess.prompts.append(text)
        raise RuntimeError("boom from agent turn")

    sess.prompt = boom  # type: ignore[assignment]
    monkeypatch.setattr(agent_step_mod, "_build_session", lambda **_kw: sess)
    # No OAuth failover — bubble immediately.
    monkeypatch.setattr(agent_step_mod, "oauth_failover_budget", lambda: 0)

    run_dir = tmp_path / "run"
    with pytest.raises(RuntimeError, match="boom from agent turn"):
        agent_step(
            agent_dir=tmp_path / "agents" / "builder",
            task="build it",
            seed_id="bead-fl1",
            rig_path=str(tmp_path),
            run_dir=run_dir,
            step="build",
            iter_n=1,
        )

    failures = run_dir / "agent_step_failures.jsonl"
    assert failures.is_file(), "agent_step_failures.jsonl was not written"
    lines = failures.read_text().splitlines()
    assert len(lines) == 1, f"expected 1 row, got {len(lines)}"
    row = _json.loads(lines[0])
    # Required keys
    for k in (
        "ts",
        "step",
        "iter",
        "role",
        "target_bead",
        "exception_class",
        "exception_msg",
        "traceback_tail",
        "bd_state_after",
        "session_uuid",
    ):
        assert k in row, f"missing key {k!r}"
    assert row["exception_class"] == "RuntimeError"
    assert "boom from agent turn" in row["exception_msg"]
    assert row["role"] == "builder"
    assert row["step"] == "build"
    assert row["iter"] == 1


def test_agent_step_failure_logger_swallows_own_errors(
    tmp_path: Path, fake_bd: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A logging failure inside _record_step_failure must NOT mask the
    original turn exception."""
    _write_prompt(tmp_path / "agents", "builder")
    fake_bd["bead-fl2"] = {
        "id": "bead-fl2",
        "status": "open",
        "title": "x",
        "metadata": {},
        "closure_reason": "",
        "notes": "",
    }
    sess = _FakeSession()

    def boom(text: str, **_kw: Any) -> str:
        sess.prompts.append(text)
        raise RuntimeError("original")

    sess.prompt = boom  # type: ignore[assignment]
    monkeypatch.setattr(agent_step_mod, "_build_session", lambda **_kw: sess)
    monkeypatch.setattr(agent_step_mod, "oauth_failover_budget", lambda: 0)

    # Force the JSONL serializer to raise — _record_step_failure must
    # swallow it so the original turn exception bubbles cleanly.
    original_dumps = agent_step_mod.json.dumps

    def explode_on_failure_row(*args: Any, **kwargs: Any) -> str:
        # Only blow up when serializing the failure row (it carries
        # `exception_class`). Other json.dumps callers stay intact.
        if args and isinstance(args[0], dict) and "exception_class" in args[0]:
            raise PermissionError("simulated logger failure")
        return original_dumps(*args, **kwargs)

    monkeypatch.setattr(agent_step_mod.json, "dumps", explode_on_failure_row)

    with pytest.raises(RuntimeError, match="original"):
        agent_step(
            agent_dir=tmp_path / "agents" / "builder",
            task="build it",
            seed_id="bead-fl2",
            rig_path=str(tmp_path),
            run_dir=tmp_path / "run-fl2",
            step="build",
            iter_n=1,
        )


# ─── bd shellout timeout → typed flow failure (prefect-orchestration-3e78) ──


def test_stamp_description_raises_typed_error_on_bd_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wedged `bd update --description` shellout surfaces a typed
    `BdShellTimeoutError` rather than hanging the agent_step setup window
    indefinitely (the indefinite-Running root cause)."""
    import subprocess

    from prefect_orchestration.beads_meta import BdShellTimeoutError

    monkeypatch.setattr(agent_step_mod, "_resolve_binary", lambda _rp=None: "bd")

    def _timeout(*_a: Any, **_kw: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="bd", timeout=30)

    monkeypatch.setattr(agent_step_mod.subprocess, "run", _timeout)

    with pytest.raises(BdShellTimeoutError, match="timed out"):
        agent_step_mod._stamp_description("bead-1", "task spec", str(tmp_path))


def test_agent_step_bd_timeout_surfaces_for_flow_outcome(
    tmp_path: Path, fake_bd: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: a bd shellout that times out inside the agent_step setup
    window (before the agent turn) raises `BdShellTimeoutError` out of
    `agent_step`. A flow-level handler modelled on the pack then writes
    `flow_outcome.json`, converting an indefinite wedge into a reapable
    failure that `po status` can see.
    """
    import json
    import subprocess

    from prefect_orchestration.beads_meta import BdShellTimeoutError

    _write_prompt(tmp_path / "agents", "builder")
    fake_bd["seed"] = {
        "id": "seed",
        "status": "open",
        "title": "s",
        "metadata": {},
        "closure_reason": "",
        "notes": "",
    }
    # Force the `_stamp_description` shellout to be reached and to wedge.
    monkeypatch.setattr(agent_step_mod, "_resolve_binary", lambda _rp=None: "bd")
    monkeypatch.setattr(agent_step_mod, "_metadata_binary", lambda _rp=None: None)

    def _timeout(*_a: Any, **_kw: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="bd", timeout=30)

    monkeypatch.setattr(agent_step_mod.subprocess, "run", _timeout)

    run_dir = tmp_path / "run"
    run_dir.mkdir()

    # Flow-level handler shape (mirrors the pack's software_dev.py top-level
    # except → _record_flow_outcome): catch the typed error, write the outcome.
    try:
        agent_step(
            agent_dir=tmp_path / "agents" / "builder",
            task="build it",
            seed_id="seed",
            rig_path=str(tmp_path),
            run_dir=run_dir,
            iter_n=1,
            step="build",
        )
        raised: Exception | None = None
    except BdShellTimeoutError as exc:  # the flow's top-level except
        raised = exc
        (run_dir / "flow_outcome.json").write_text(
            json.dumps(
                {"ok": False, "exception_class": type(exc).__name__, "msg": str(exc)}
            )
        )

    assert isinstance(raised, BdShellTimeoutError)
    outcome_path = run_dir / "flow_outcome.json"
    assert outcome_path.exists()
    outcome = json.loads(outcome_path.read_text())
    assert outcome["ok"] is False
    assert outcome["exception_class"] == "BdShellTimeoutError"
