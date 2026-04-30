"""Per-role runtime config precedence (`role_config.py`) — model · effort · start_command.

Resolution order, most-specific wins:

    per-role config.toml  >  PO_*_CLI env (set by `po run` flags)  >
    PO_*  shell env  >  None (caller falls back to hardcoded default)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import prefect_orchestration.agent_step as agent_step_mod
from prefect_orchestration.role_config import (
    RoleConfigLoadError,
    RoleRuntime,
    load_role_config,
    resolve_role_runtime,
)


def _write_config(agent_dir: Path, body: str) -> None:
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "config.toml").write_text(body)


# ─── load_role_config ───────────────────────────────────────────────


def test_load_role_config_missing_file(tmp_path: Path) -> None:
    """No config.toml on disk → empty RoleRuntime, no raise."""
    rt = load_role_config(tmp_path)
    assert rt == RoleRuntime()


def test_load_role_config_happy_path(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        'model = "haiku"\neffort = "max"\nstart_command = "claude --foo"\n',
    )
    rt = load_role_config(tmp_path)
    assert rt == RoleRuntime(model="haiku", effort="max", start_command="claude --foo")


def test_load_role_config_partial(tmp_path: Path) -> None:
    """Subset of fields → unset fields stay None."""
    _write_config(tmp_path, 'effort = "low"\n')
    rt = load_role_config(tmp_path)
    assert rt.effort == "low"
    assert rt.model is None
    assert rt.start_command is None


def test_load_role_config_unknown_keys_ignored(tmp_path: Path) -> None:
    """Forward-compat: unrecognized keys silently dropped."""
    _write_config(tmp_path, 'model = "opus"\nfuture_thing = "ignored"\n')
    rt = load_role_config(tmp_path)
    assert rt == RoleRuntime(model="opus")


def test_load_role_config_malformed_toml(tmp_path: Path) -> None:
    _write_config(tmp_path, "this is not = valid = toml\n")
    with pytest.raises(RoleConfigLoadError) as exc:
        load_role_config(tmp_path)
    assert "config.toml" in str(exc.value)


def test_load_role_config_wrong_value_type(tmp_path: Path) -> None:
    """Non-string value for a known knob raises with field name."""
    _write_config(tmp_path, "model = 42\n")
    with pytest.raises(RoleConfigLoadError) as exc:
        load_role_config(tmp_path)
    assert "model" in str(exc.value)


# ─── resolve_role_runtime: precedence table ─────────────────────────


@pytest.mark.parametrize(
    "knob, env_var",
    [
        ("model", "PO_MODEL"),
        ("effort", "PO_EFFORT"),
        ("start_command", "PO_START_COMMAND"),
    ],
)
def test_per_role_config_beats_cli_flag(
    tmp_path: Path, knob: str, env_var: str
) -> None:
    """config.toml value wins over PO_*_CLI which wins over PO_*."""
    _write_config(tmp_path, f'{knob} = "from-config"\n')
    env = {f"{env_var}_CLI": "from-cli", env_var: "from-shell"}
    rt = resolve_role_runtime(tmp_path, env=env)
    assert getattr(rt, knob) == "from-config"


@pytest.mark.parametrize(
    "knob, env_var",
    [
        ("model", "PO_MODEL"),
        ("effort", "PO_EFFORT"),
        ("start_command", "PO_START_COMMAND"),
    ],
)
def test_cli_flag_beats_shell_env(tmp_path: Path, knob: str, env_var: str) -> None:
    """No config.toml: PO_*_CLI wins over PO_*."""
    env = {f"{env_var}_CLI": "from-cli", env_var: "from-shell"}
    rt = resolve_role_runtime(tmp_path, env=env)
    assert getattr(rt, knob) == "from-cli"


@pytest.mark.parametrize(
    "knob, env_var",
    [
        ("model", "PO_MODEL"),
        ("effort", "PO_EFFORT"),
        ("start_command", "PO_START_COMMAND"),
    ],
)
def test_shell_env_only(tmp_path: Path, knob: str, env_var: str) -> None:
    """Only PO_* set → that's what we get."""
    env = {env_var: "from-shell"}
    rt = resolve_role_runtime(tmp_path, env=env)
    assert getattr(rt, knob) == "from-shell"


def test_nothing_set_returns_all_none(tmp_path: Path) -> None:
    rt = resolve_role_runtime(tmp_path, env={})
    assert rt == RoleRuntime()


def test_independent_axes(tmp_path: Path) -> None:
    """Each knob resolves independently — config wins one, env wins another."""
    _write_config(tmp_path, 'model = "haiku"\n')
    env = {"PO_EFFORT_CLI": "low", "PO_START_COMMAND": "claude --foo"}
    rt = resolve_role_runtime(tmp_path, env=env)
    assert rt.model == "haiku"
    assert rt.effort == "low"
    assert rt.start_command == "claude --foo"


# ─── _build_session integration ─────────────────────────────────────


class _RecordingBackend:
    """Minimal backend stub that captures kwargs for assertions."""

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.start_command = kwargs.get("start_command", "claude --default")


def _make_role(tmp_path: Path, role: str, *, config_body: str | None = None) -> Path:
    role_dir = tmp_path / "agents" / role
    role_dir.mkdir(parents=True, exist_ok=True)
    (role_dir / "prompt.md").write_text("you are " + role)
    if config_body is not None:
        (role_dir / "config.toml").write_text(config_body)
    return role_dir


def test_build_session_per_role_config_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-role config.toml beats CLI env even when both are set."""
    role_dir = _make_role(tmp_path, "linter", config_body='model = "haiku"\n')
    monkeypatch.setenv("PO_MODEL_CLI", "sonnet")

    sess = agent_step_mod._build_session(
        seed_id="seed",
        role="linter",
        rig_path=str(tmp_path),
        agent_dir=role_dir,
        run_dir=tmp_path / "rundir",
        backend=_RecordingBackend,
        dry_run=False,
    )
    assert sess.model == "haiku"


def test_build_session_cli_env_used_when_no_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    role_dir = _make_role(tmp_path, "builder")
    monkeypatch.setenv("PO_MODEL_CLI", "sonnet")
    monkeypatch.setenv("PO_EFFORT_CLI", "low")

    sess = agent_step_mod._build_session(
        seed_id="seed",
        role="builder",
        rig_path=str(tmp_path),
        agent_dir=role_dir,
        run_dir=tmp_path / "rundir",
        backend=_RecordingBackend,
        dry_run=False,
    )
    assert sess.model == "sonnet"
    assert sess.effort == "low"


def test_build_session_start_command_threads_to_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`start_command` config flows into the backend constructor kwargs."""
    role_dir = _make_role(
        tmp_path,
        "builder",
        config_body='start_command = "claude --custom"\n',
    )

    sess = agent_step_mod._build_session(
        seed_id="seed",
        role="builder",
        rig_path=str(tmp_path),
        agent_dir=role_dir,
        run_dir=tmp_path / "rundir",
        backend=_RecordingBackend,
        dry_run=False,
    )
    assert sess.backend.init_kwargs.get("start_command") == "claude --custom"


def test_build_session_no_overrides_uses_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing set anywhere → AgentSession keeps its hardcoded defaults."""
    # Scrub any inherited env vars.
    for var in (
        "PO_MODEL",
        "PO_MODEL_CLI",
        "PO_EFFORT",
        "PO_EFFORT_CLI",
        "PO_START_COMMAND",
        "PO_START_COMMAND_CLI",
    ):
        monkeypatch.delenv(var, raising=False)
    role_dir = _make_role(tmp_path, "doer")

    sess = agent_step_mod._build_session(
        seed_id="seed",
        role="doer",
        rig_path=str(tmp_path),
        agent_dir=role_dir,
        run_dir=tmp_path / "rundir",
        backend=_RecordingBackend,
        dry_run=False,
    )
    assert sess.model == "opus"  # AgentSession default
    assert sess.effort is None
    # Backend got no start_command kwarg → falls back to its own default.
    assert "start_command" not in sess.backend.init_kwargs
