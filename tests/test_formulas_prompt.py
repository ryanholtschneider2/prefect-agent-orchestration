"""agent-step (role + existing bead) + the shared mint_seed_bead helper.

Inline-prompt dispatch lives in the `prompt` formula now (--prompt/--agent/
--fresh); agent-step is purely "run a named role against an existing bead".
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import prefect_orchestration.formulas as formulas_mod
import prefect_orchestration.prompt_formula as prompt_formula_mod
from prefect_orchestration import beads_meta
from prefect_orchestration.formulas import agent_step_flow, discover_agent_dir
from prefect_orchestration.prompt_formula import _pick_backend_factory


def _fake_result() -> SimpleNamespace:
    return SimpleNamespace(
        bead_id="b1", verdict="", summary="ok", from_cache=False, closed_by="agent"
    )


def test_prompt_runner_agent_ships() -> None:
    """The generic prompt-runner identity is shipped + discoverable in core."""
    d = discover_agent_dir("prompt-runner")
    assert (d / "prompt.md").is_file()


def test_agent_step_runs_named_role_against_the_bead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict = {}
    monkeypatch.setattr(
        formulas_mod, "agent_step", lambda **kw: calls.update(kw) or _fake_result()
    )
    monkeypatch.setattr(
        formulas_mod, "discover_agent_dir", lambda role: Path(f"/agents/{role}")
    )

    agent_step_flow(issue_id="real-bead", rig="r", rig_path="/tmp", agent="triager")

    assert calls["seed_id"] == "real-bead"
    assert calls["task"] is None  # bead description IS the task spec


def test_agent_step_errors_without_a_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(formulas_mod, "_read_meta", lambda *a, **k: None)
    with pytest.raises(ValueError, match="no agent"):
        agent_step_flow(issue_id="x", rig="r", rig_path="/tmp")


def test_mint_seed_bead_falls_back_to_auto_id_on_prefix_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(beads_meta, "_bd_available", lambda: True)
    seen: dict = {"n": 0}

    def fake_run(cmd, **_kw):
        seen["n"] += 1
        joined = " ".join(cmd)
        if seen["n"] == 1:
            assert "--id=" in joined  # first attempt requests an explicit id
            return SimpleNamespace(
                returncode=1, stdout="", stderr="error: id prefix mismatch"
            )
        assert "--id=" not in joined  # retry lets bd assign the id
        return SimpleNamespace(
            returncode=0, stdout="✓ Created issue: rig-abc — do x\n", stderr=""
        )

    monkeypatch.setattr(beads_meta.subprocess, "run", fake_run)

    assert beads_meta.mint_seed_bead("daily", "do x", rig_path="/tmp/rig") == "rig-abc"


def test_mint_seed_bead_uses_requested_id_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(beads_meta, "_bd_available", lambda: True)
    monkeypatch.setattr(
        beads_meta.subprocess,
        "run",
        lambda cmd, **_kw: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
    )
    got = beads_meta.mint_seed_bead("feature-x", "the goal", rig_path="/tmp/rig")
    assert got.startswith("feature-x-")  # <prefix>-<utc-timestamp>


def test_prompt_formula_supports_cursor_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PO_BACKEND", "cursor-cli")
    from prefect_orchestration.agent_session import CursorCliBackend

    assert _pick_backend_factory(dry_run=False) is CursorCliBackend


def test_prompt_formula_materializes_capacity_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    fallback = object()

    class _Session:
        session_id = None

        def __init__(self, **kwargs: Any):
            captured.update(kwargs)

        def prompt(self, _text: str) -> str:
            return "ok"

    monkeypatch.setattr(prompt_formula_mod, "AgentSession", _Session)
    monkeypatch.setattr(
        prompt_formula_mod,
        "get_run_logger",
        lambda: SimpleNamespace(info=lambda *a: None, warning=lambda *a: None),
    )
    monkeypatch.setattr(
        prompt_formula_mod, "_pick_backend_factory", lambda _dry: object
    )
    monkeypatch.setattr(prompt_formula_mod, "_make_backend", lambda *a, **k: object())
    monkeypatch.setattr(
        prompt_formula_mod,
        "materialize_capacity_policy",
        lambda **kwargs: captured.update({"capacity_call": kwargs}) or (2, (fallback,)),
    )
    monkeypatch.setattr(
        prompt_formula_mod, "create_markdown_artifact", lambda **k: None
    )

    prompt_formula_mod.prompt_run.fn(
        prompt="do it",
        rig_path=str(tmp_path),
        create_bead=False,
    )

    assert captured["capacity_retries"] == 2
    assert captured["runtime_fallbacks"] == (fallback,)
    assert captured["capacity_call"]["role"] == "general"
