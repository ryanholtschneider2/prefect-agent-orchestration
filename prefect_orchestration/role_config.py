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

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class RoleConfigLoadError(ValueError):
    """Raised when `<agent_dir>/config.toml` is malformed."""


class CapacityPolicyConfigError(ValueError):
    """Raised when explicit capacity retry/fallback transport is malformed."""


@dataclass(frozen=True)
class RoleRuntime:
    """Runtime knobs for one role's Claude invocation. None = unset at this layer."""

    model: str | None = None
    effort: str | None = None
    start_command: str | None = None


@dataclass(frozen=True)
class RuntimeFallbackSpec:
    """Validated operator-supplied runtime candidate; never inferred by PO."""

    backend: str
    model: str
    effort: str | None = None
    start_command: str | None = None
    account: str | None = None
    account_class: str | None = None
    label: str = ""


@dataclass(frozen=True)
class CapacityPolicy:
    retries: int = 0
    fallbacks: tuple[RuntimeFallbackSpec, ...] = ()


_FIELD_NAMES = frozenset(("model", "effort", "start_command"))
_FALLBACK_FIELDS = frozenset(
    (
        "backend",
        "model",
        "effort",
        "start_command",
        "account",
        "account_class",
        "label",
    )
)
_FALLBACK_BACKENDS = frozenset(
    (
        "cli",
        "tmux",
        "codex-cli",
        "codex-tmux",
        "codex-tmux-stream",
        "cursor-cli",
        "cursor-tmux",
    )
)
MAX_CAPACITY_RETRIES = 3
MAX_RUNTIME_FALLBACKS = 4


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


def resolve_capacity_policy(*, env: Mapping[str, str] | None = None) -> CapacityPolicy:
    """Resolve explicit capacity transport from ``PO_*`` environment values.

    ``PO_CAPACITY_RETRIES`` is an integer in ``0..3``. ``PO_RUNTIME_FALLBACKS``
    is a JSON array of ordered runtime objects. Both default to disabled; PO
    never invents a provider, model, account, or fallback.
    """
    if env is None:
        import os

        env = os.environ
    raw_retries = env.get("PO_CAPACITY_RETRIES", "0").strip()
    try:
        retries = int(raw_retries)
    except ValueError as exc:
        raise CapacityPolicyConfigError(
            "PO_CAPACITY_RETRIES must be an integer between 0 and "
            f"{MAX_CAPACITY_RETRIES}; got {raw_retries!r}"
        ) from exc
    if not 0 <= retries <= MAX_CAPACITY_RETRIES:
        raise CapacityPolicyConfigError(
            "PO_CAPACITY_RETRIES must be between 0 and "
            f"{MAX_CAPACITY_RETRIES}; got {retries}"
        )

    raw_fallbacks = env.get("PO_RUNTIME_FALLBACKS", "").strip()
    if not raw_fallbacks:
        return CapacityPolicy(retries=retries)
    try:
        payload = json.loads(raw_fallbacks)
    except json.JSONDecodeError as exc:
        raise CapacityPolicyConfigError(
            f"PO_RUNTIME_FALLBACKS must be valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, list):
        raise CapacityPolicyConfigError("PO_RUNTIME_FALLBACKS must be a JSON array")
    if len(payload) > MAX_RUNTIME_FALLBACKS:
        raise CapacityPolicyConfigError(
            "PO_RUNTIME_FALLBACKS supports at most "
            f"{MAX_RUNTIME_FALLBACKS} entries; got {len(payload)}"
        )

    fallbacks: list[RuntimeFallbackSpec] = []
    for index, raw in enumerate(payload):
        prefix = f"PO_RUNTIME_FALLBACKS[{index}]"
        if not isinstance(raw, dict):
            raise CapacityPolicyConfigError(f"{prefix} must be an object")
        unknown = set(raw) - _FALLBACK_FIELDS
        if unknown:
            raise CapacityPolicyConfigError(
                f"{prefix} has unknown field(s): {', '.join(sorted(unknown))}"
            )
        backend = raw.get("backend")
        model = raw.get("model")
        if not isinstance(backend, str) or backend not in _FALLBACK_BACKENDS:
            raise CapacityPolicyConfigError(
                f"{prefix}.backend must be one of: "
                f"{', '.join(sorted(_FALLBACK_BACKENDS))}"
            )
        if not isinstance(model, str) or not model.strip():
            raise CapacityPolicyConfigError(
                f"{prefix}.model must be a non-empty string"
            )
        values: dict[str, str | None] = {}
        for field_name in _FALLBACK_FIELDS - {"backend", "model"}:
            value = raw.get(field_name)
            if value is not None and not isinstance(value, str):
                raise CapacityPolicyConfigError(
                    f"{prefix}.{field_name} must be a string when provided"
                )
            values[field_name] = value.strip() if isinstance(value, str) else None
        fallbacks.append(
            RuntimeFallbackSpec(
                backend=backend,
                model=model.strip(),
                effort=values["effort"],
                start_command=values["start_command"],
                account=values["account"],
                account_class=values["account_class"],
                label=values["label"] or "",
            )
        )
    return CapacityPolicy(retries=retries, fallbacks=tuple(fallbacks))


__all__ = [
    "RoleConfigLoadError",
    "RoleRuntime",
    "CapacityPolicy",
    "CapacityPolicyConfigError",
    "RuntimeFallbackSpec",
    "load_role_config",
    "resolve_capacity_policy",
    "resolve_role_runtime",
]
