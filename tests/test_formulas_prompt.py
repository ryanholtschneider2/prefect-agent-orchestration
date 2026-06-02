"""agent-step (role + existing bead) + the shared mint_seed_bead helper.

Inline-prompt dispatch lives in the `prompt` formula now (--prompt/--agent/
--fresh); agent-step is purely "run a named role against an existing bead".
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import prefect_orchestration.formulas as formulas_mod
from prefect_orchestration import beads_meta
from prefect_orchestration.formulas import agent_step_flow, discover_agent_dir


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
