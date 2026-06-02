"""Inline-prompt path of the `agent-step` formula (prompt-runner)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import prefect_orchestration.formulas as formulas_mod
from prefect_orchestration.formulas import (
    _mint_prompt_seed,
    agent_step_flow,
    discover_agent_dir,
)


def _fake_result() -> SimpleNamespace:
    return SimpleNamespace(
        bead_id="b1", verdict="", summary="ok", from_cache=False, closed_by="agent"
    )


def test_prompt_runner_agent_ships() -> None:
    """The generic prompt-runner identity is shipped + discoverable in core."""
    d = discover_agent_dir("prompt-runner")
    assert (d / "prompt.md").is_file()


def test_inline_prompt_dry_run_uses_prompt_runner_and_skips_mint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict = {}
    monkeypatch.setattr(
        formulas_mod, "agent_step", lambda **kw: calls.update(kw) or _fake_result()
    )
    monkeypatch.setattr(
        formulas_mod,
        "_mint_prompt_seed",
        lambda *a, **k: pytest.fail("dry_run must not mint a bead"),
    )

    out = agent_step_flow(
        issue_id="x", rig="r", rig_path="/tmp", prompt="do the thing", dry_run=True
    )

    assert calls["seed_id"] == "x"  # dry_run operates on issue_id directly
    assert calls["task"] == "do the thing"
    assert Path(calls["agent_dir"]).name == "prompt-runner"
    assert out["closed_by"] == "agent"


def test_inline_prompt_mints_fresh_seed_when_not_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict = {}
    monkeypatch.setattr(
        formulas_mod, "agent_step", lambda **kw: calls.update(kw) or _fake_result()
    )
    monkeypatch.setattr(
        formulas_mod,
        "_mint_prompt_seed",
        lambda issue_id, prompt, rig_path: "minted-123",
    )

    agent_step_flow(issue_id="x", rig="r", rig_path="/tmp", prompt="p")

    assert calls["seed_id"] == "minted-123"  # fresh per-run bead, not issue_id
    assert calls["task"] == "p"


def test_no_prompt_keeps_role_from_arg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a prompt the original role+bead path is used (no minting)."""
    calls: dict = {}
    monkeypatch.setattr(
        formulas_mod, "agent_step", lambda **kw: calls.update(kw) or _fake_result()
    )
    monkeypatch.setattr(
        formulas_mod, "discover_agent_dir", lambda role: Path(f"/agents/{role}")
    )

    agent_step_flow(
        issue_id="real-bead", rig="r", rig_path="/tmp", agent="prompt-runner"
    )

    assert calls["seed_id"] == "real-bead"
    assert calls["task"] is None  # bead description IS the task spec


def test_mint_prompt_seed_falls_back_to_auto_id_on_prefix_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(formulas_mod.shutil, "which", lambda _x: "/usr/bin/bd")
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

    monkeypatch.setattr(formulas_mod.subprocess, "run", fake_run)

    assert _mint_prompt_seed("daily", "do x", "/tmp/rig") == "rig-abc"
