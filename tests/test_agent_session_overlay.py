"""AgentSession ↔ pack_overlay wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from prefect_orchestration import agent_session as agent_session_mod
from prefect_orchestration.agent_session import AgentSession


class _RecordingBackend:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None,
        cwd: Path,
        fork: bool = False,
        model: str = "opus",
    ) -> tuple[str, str]:
        self.calls.append(prompt)
        return "ok", session_id or "sid-1"


@pytest.fixture
def patched_materialize(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture each materialize_packs call so tests can assert on args."""
    captured: list[dict[str, Any]] = []

    def fake(
        cwd: Path, *, role: str | None, overlay: bool, skills: bool, **kw: Any
    ) -> dict:
        captured.append(
            {"cwd": cwd, "role": role, "overlay": overlay, "skills": skills}
        )
        return {}

    import prefect_orchestration.pack_overlay as po

    monkeypatch.setattr(po, "materialize_packs", fake)
    # AgentSession imports lazily; patch the module attribute so the
    # `from ... import materialize_packs` inside the method picks ours up.
    monkeypatch.setattr(
        agent_session_mod, "_materialize_packs_for_test", fake, raising=False
    )
    return captured


def test_prompt_triggers_materialization_once(
    tmp_path: Path, patched_materialize: list[dict[str, Any]]
) -> None:
    backend = _RecordingBackend()
    sess = AgentSession(role="builder", repo_path=tmp_path, backend=backend)

    sess.prompt("first turn")
    sess.prompt("second turn")

    assert len(patched_materialize) == 1
    call = patched_materialize[0]
    assert call["cwd"] == tmp_path
    assert call["role"] == "builder"
    assert call["overlay"] is True
    assert call["skills"] is True


def test_opt_out_overlay_and_skills(
    tmp_path: Path, patched_materialize: list[dict[str, Any]]
) -> None:
    backend = _RecordingBackend()
    sess = AgentSession(
        role="critic",
        repo_path=tmp_path,
        backend=backend,
        overlay=False,
        skills=False,
    )

    sess.prompt("hi")

    # Both off → short-circuit, no call recorded.
    assert patched_materialize == []


def test_opt_out_only_overlay(
    tmp_path: Path, patched_materialize: list[dict[str, Any]]
) -> None:
    backend = _RecordingBackend()
    sess = AgentSession(
        role="critic",
        repo_path=tmp_path,
        backend=backend,
        overlay=False,
        skills=True,
    )

    sess.prompt("hi")

    assert len(patched_materialize) == 1
    assert patched_materialize[0]["overlay"] is False
    assert patched_materialize[0]["skills"] is True


def test_materialization_failure_does_not_block_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import prefect_orchestration.pack_overlay as po

    def boom(*args: Any, **kw: Any) -> dict:
        raise RuntimeError("disk gremlin")

    monkeypatch.setattr(po, "materialize_packs", boom)
    backend = _RecordingBackend()
    sess = AgentSession(role="builder", repo_path=tmp_path, backend=backend)

    # Should not raise.
    result = sess.prompt("turn one")
    assert result == "ok"
    assert backend.calls == ["turn one"]
