"""Per-role secret injection for AgentSession backends.

Convention: secrets are keyed `<PREFIX>_<ROLE_KEY>` in the orchestrator's
environment (or per-rig `.env` overlay). At session-launch time, the
provider returns a re-keyed dict containing only that role's subset
(e.g. `{"SLACK_TOKEN": "xoxb-…"}`), which the backend merges into the
child env after stripping every other role's scoped vars.

Three impls:

* `EnvSecretProvider`        — scans `os.environ`
* `DotenvSecretProvider`     — parses a `.env` file (no transitive dep)
* `ChainSecretProvider`      — first-hit-wins precedence

All implement `SecretProvider` (runtime-checkable Protocol). A future
`VaultSecretProvider` plugs in by implementing the same one method.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

DEFAULT_PREFIXES: tuple[str, ...] = (
    "SLACK_TOKEN",
    "GMAIL_CREDS",
    "ATTIO_TOKEN",
    "CALENDAR_CREDS",
)

_NORMALIZE_RE = re.compile(r"[^A-Z0-9]+")


def role_env_key(role: str) -> str:
    """Normalize a role name for env-var lookup.

    Hyphens, dots, spaces and any non-alphanumeric run collapse to a
    single underscore; result is uppercased. `plan-critic` →
    `PLAN_CRITIC`, `prefect-orchestration-4ja.1` →
    `PREFECT_ORCHESTRATION_4JA_1`. Symmetric: docs and lookup share
    this normalizer so `.env` keys never silently miss.
    """
    upper = role.upper()
    collapsed = _NORMALIZE_RE.sub("_", upper).strip("_")
    return collapsed


@runtime_checkable
class SecretProvider(Protocol):
    """Returns the re-keyed env subset to inject into a role's session."""

    def get_role_env(self, role: str) -> dict[str, str]: ...


def _rekey_for_role(
    raw: dict[str, str], role: str, prefixes: Iterable[str]
) -> dict[str, str]:
    """Pick `<PREFIX>_<ROLE_KEY>` keys from `raw` and return `{<PREFIX>: val}`."""
    role_key = role_env_key(role)
    out: dict[str, str] = {}
    for prefix in prefixes:
        scoped = f"{prefix}_{role_key}"
        if scoped in raw and raw[scoped]:
            out[prefix] = raw[scoped]
    return out


@dataclass
class EnvSecretProvider:
    """Read role-scoped secrets from `os.environ`."""

    prefixes: tuple[str, ...] = DEFAULT_PREFIXES

    def get_role_env(self, role: str) -> dict[str, str]:
        return _rekey_for_role(dict(os.environ), role, self.prefixes)

    def __repr__(self) -> str:  # avoid printing values if env is dumped
        return f"EnvSecretProvider(prefixes={self.prefixes!r})"


def _parse_dotenv(text: str) -> dict[str, str]:
    """Tiny `.env` parser — `KEY=val`, optional surrounding `"`/`'`, `#` comments.

    Does NOT handle: escaped quotes, multi-line values, `export ` prefix
    (stripped if present), or shell expansion. Intentional — keeps the
    dependency surface zero. If you need any of those, swap in
    `python-dotenv` and it implements the same Protocol.
    """
    out: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        # Strip a single surrounding pair of quotes (matched).
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        out[key] = val
    return out


@dataclass
class DotenvSecretProvider:
    """Read role-scoped secrets from a `.env` file."""

    path: Path
    prefixes: tuple[str, ...] = DEFAULT_PREFIXES

    def get_role_env(self, role: str) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            raw = _parse_dotenv(self.path.read_text())
        except OSError:
            return {}
        return _rekey_for_role(raw, role, self.prefixes)

    def __repr__(self) -> str:
        return f"DotenvSecretProvider(path={self.path!r}, prefixes={self.prefixes!r})"


@dataclass
class ChainSecretProvider:
    """First-hit-wins overlay across multiple providers.

    `ChainSecretProvider([dotenv, env])` — dotenv values win over env;
    env fills in anything the dotenv didn't define for that role.
    """

    providers: list[SecretProvider] = field(default_factory=list)

    def get_role_env(self, role: str) -> dict[str, str]:
        merged: dict[str, str] = {}
        for p in self.providers:
            sub = p.get_role_env(role)
            for k, v in sub.items():
                merged.setdefault(k, v)
        return merged

    def __repr__(self) -> str:
        return f"ChainSecretProvider(providers={self.providers!r})"


def strip_role_scoped(env: dict[str, str], prefixes: Iterable[str]) -> dict[str, str]:
    """Remove every `<PREFIX>_*` key from `env` (in place semantics).

    Used by backends to scrub the orchestrator's per-role secrets out
    of child env *before* overlaying the current role's re-keyed subset.
    Without this, role A's tmux session would inherit `SLACK_TOKEN_B`
    from the orchestrator and could read role B's token.
    """
    drop: list[str] = []
    for key in env:
        for prefix in prefixes:
            if key.startswith(f"{prefix}_"):
                drop.append(key)
                break
    for key in drop:
        env.pop(key, None)
    return env


def resolve_role_env(
    role: str,
    *,
    base_env: dict[str, str],
    provider: SecretProvider | None,
    prefixes: Iterable[str] = DEFAULT_PREFIXES,
) -> dict[str, str]:
    """Compute the child env for a role's session.

    Steps:
      1. Copy `base_env`.
      2. Strip every `<PREFIX>_*` key (no peer-role leakage).
      3. Overlay the provider's re-keyed subset for this role.

    When `provider` is `None`, step 3 is a no-op — but step 2 still
    runs, so peer-role secrets from the orchestrator's env never reach
    the child. Pass `provider=None` and an empty `prefixes` tuple to
    fully opt out (and behave exactly like today).
    """
    env = dict(base_env)
    strip_role_scoped(env, prefixes)
    if provider is not None:
        env.update(provider.get_role_env(role))
    return env
