"""Per-role runtime config (`agents/<role>/config.toml`) — model, effort, start_command.

Distinct from `identity.toml` (persona: name, email, slack, mail_agent_name,
prompt-display model). `config.toml` controls the **runtime** Claude
invocation: which model the CLI actually uses, what `--effort` flag is
passed, and what argv prefix invokes claude.

Schema (all keys optional, top-level / flat)::

    model         = "haiku"
    effort        = "max"
    start_command = "claude --dangerously-skip-permissions"

Precedence (most-specific wins): per-role config > CLI flag > env var > default.
The CLI flag layer is implemented by stamping `PO_<KNOB>_CLI` env vars in
`cli.py::run` so `resolve_role_runtime` can distinguish flag-source from
shell-source without reading argv directly.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class RoleConfigLoadError(ValueError):
    """Raised when `<agent_dir>/config.toml` is malformed."""


@dataclass(frozen=True)
class RoleRuntime:
    """Runtime knobs for one role's Claude invocation. None = unset at this layer."""

    model: str | None = None
    effort: str | None = None
    start_command: str | None = None


_FIELD_NAMES = frozenset(("model", "effort", "start_command"))


def load_role_config(agent_dir: Path) -> RoleRuntime:
    """Read `<agent_dir>/config.toml`. Missing file → empty `RoleRuntime`.

    Unknown keys are ignored (forward-compat). Non-string values for known
    keys raise `RoleConfigLoadError`.
    """
    path = Path(agent_dir) / "config.toml"
    if not path.is_file():
        return RoleRuntime()
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise RoleConfigLoadError(f"malformed TOML in {path}: {e}") from e
    if not isinstance(data, dict):
        raise RoleConfigLoadError(f"{path}: expected a top-level table")
    typed: dict[str, str] = {}
    for k, v in data.items():
        if k not in _FIELD_NAMES:
            continue
        if not isinstance(v, str):
            raise RoleConfigLoadError(
                f"{path}: field {k!r} must be a string (got {type(v).__name__})"
            )
        typed[k] = v
    return RoleRuntime(**typed)


def resolve_role_runtime(
    agent_dir: Path,
    *,
    env: Mapping[str, str] | None = None,
) -> RoleRuntime:
    """Resolve precedence: per-role config > CLI env (`PO_*_CLI`) > shell env (`PO_*`) > None.

    `env` defaults to `os.environ`. Each field of the returned `RoleRuntime`
    is None when no layer set it; callers fall back to hardcoded defaults
    (e.g. `model="opus"`, `start_command="claude --dangerously-skip-permissions"`).
    """
    if env is None:
        import os

        env = os.environ
    cfg = load_role_config(agent_dir)
    return RoleRuntime(
        model=cfg.model or env.get("PO_MODEL_CLI") or env.get("PO_MODEL") or None,
        effort=cfg.effort or env.get("PO_EFFORT_CLI") or env.get("PO_EFFORT") or None,
        start_command=cfg.start_command
        or env.get("PO_START_COMMAND_CLI")
        or env.get("PO_START_COMMAND")
        or None,
    )


__all__ = [
    "RoleConfigLoadError",
    "RoleRuntime",
    "load_role_config",
    "resolve_role_runtime",
]
