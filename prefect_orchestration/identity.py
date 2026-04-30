"""Per-role agent identity (`agents/<role>/identity.toml`).

Each role gets a stable identity (name, email, slack handle,
mail-server agent_name, model preference) shipped by the pack at
``<agents_dir>/<role>/identity.toml`` and optionally overridden by
the rig at ``<rig>/.claude/agents/<role>/identity.toml`` (per-field
merge, rig wins per key).

Used by ``render_template`` to auto-prepend a ``<self>...</self>``
block to every rendered prompt and to expose ``{{agent_name}}`` /
``{{agent_email}}`` / ``{{agent_slack}}`` / ``{{agent_mail_name}}`` /
``{{agent_model}}`` substitution vars (so prompts can write
``register_agent name="{{agent_name}}"``).

Schema (all fields optional)::

    [identity]
    name             = "acquisitions-bot"
    email            = "acquisitions@example.com"
    slack            = "@acquisitions-bot"
    mail_agent_name  = "acquisitions-bot"   # falls back to name
    model            = "opus"
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


class IdentityLoadError(ValueError):
    """Raised when an ``identity.toml`` file is malformed."""


@dataclass(frozen=True)
class Identity:
    """Stable per-role agent identity. All fields optional."""

    name: str | None = None
    email: str | None = None
    slack: str | None = None
    mail_agent_name: str | None = None
    model: str | None = None

    def is_empty(self) -> bool:
        return all(getattr(self, f.name) is None for f in fields(self))

    @property
    def effective_mail_agent_name(self) -> str | None:
        """``mail_agent_name`` if set, else ``name``."""
        return self.mail_agent_name or self.name


_FIELD_NAMES = frozenset(f.name for f in fields(Identity))


def _read_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise IdentityLoadError(f"malformed TOML in {path}: {e}") from e
    section = data.get("identity")
    if section is None:
        # Allow flat-table form for tolerance, but prefer [identity].
        section = data
    if not isinstance(section, dict):
        raise IdentityLoadError(
            f"{path}: expected an [identity] table mapping, got {type(section).__name__}"
        )
    return {k: v for k, v in section.items() if k in _FIELD_NAMES}


def load_identity(
    agents_dir: Path,
    role: str,
    *,
    rig_path: Path | None = None,
) -> Identity | None:
    """Load identity for ``role``.

    Reads ``<agents_dir>/<role>/identity.toml`` (pack default) and
    optionally ``<rig_path>/.claude/agents/<role>/identity.toml`` (rig
    overlay). Per-field merge: rig wins per key, pack fills the rest.

    Returns ``None`` when neither file exists, so callers can preserve
    backward-compatible behavior (no ``<self>`` block emitted).
    """
    pack_path = Path(agents_dir) / role / "identity.toml"
    rig_overlay_path = (
        Path(rig_path) / ".claude" / "agents" / role / "identity.toml"
        if rig_path is not None
        else None
    )

    have_pack = pack_path.is_file()
    have_rig = rig_overlay_path is not None and rig_overlay_path.is_file()
    if not have_pack and not have_rig:
        return None

    merged: dict[str, object] = {}
    if have_pack:
        merged.update(_read_toml(pack_path))
    if have_rig:
        assert rig_overlay_path is not None
        merged.update(_read_toml(rig_overlay_path))

    typed: dict[str, str | None] = {}
    for k, v in merged.items():
        if v is None:
            typed[k] = None
        elif isinstance(v, str):
            typed[k] = v
        else:
            raise IdentityLoadError(
                f"identity field {k!r} must be a string (got {type(v).__name__})"
            )
    return Identity(**typed)


def format_self_block(identity: Identity) -> str:
    """Render an ``<self>...</self>`` block for non-empty fields.

    Returns ``""`` when ``identity`` carries no non-None fields, so
    callers can ``"".join`` unconditionally.
    """
    if identity.is_empty():
        return ""
    lines = ["<self>"]
    if identity.name:
        lines.append(f"You are {identity.name}.")
    if identity.email:
        lines.append(f"email: {identity.email}")
    if identity.slack:
        lines.append(f"slack: {identity.slack}")
    mail_name = identity.effective_mail_agent_name
    if mail_name:
        lines.append(f"mail_agent_name: {mail_name}")
    if identity.model:
        lines.append(f"model: {identity.model}")
    lines.append("</self>")
    lines.append("")  # trailing blank line
    return "\n".join(lines) + "\n"


def identity_vars(identity: Identity | None) -> dict[str, str]:
    """Return ``{{agent_*}}`` substitution vars for non-None fields.

    Caller-provided vars take precedence — the renderer should merge
    these *behind* the user's kwargs, not on top.
    """
    if identity is None:
        return {}
    out: dict[str, str] = {}
    if identity.name:
        out["agent_name"] = identity.name
    if identity.email:
        out["agent_email"] = identity.email
    if identity.slack:
        out["agent_slack"] = identity.slack
    mail_name = identity.effective_mail_agent_name
    if mail_name:
        out["agent_mail_name"] = mail_name
    if identity.model:
        out["agent_model"] = identity.model
    return out
