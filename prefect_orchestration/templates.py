"""Generic `{{var}}` template renderer for agent prompt files.

Lives in core because every formula needs it, but the `agents_dir` is
caller-supplied — core ships no prompts of its own.

Layout (per `engdocs/principles.md` — "Prompt authoring convention"):

    <pack>/po_formulas/agents/
      triager/
        prompt.md
        identity.toml      (optional — see prefect_orchestration.identity)
      builder/
        prompt.md
      ...

`render_template(agents_dir, "triager", ...)` reads
`<agents_dir>/triager/prompt.md`. Plain markdown — no Jinja, no fragments.

When ``identity.toml`` is present (pack default and/or rig overlay at
``<rig>/.claude/agents/<role>/identity.toml``), a ``<self>...</self>``
block is auto-prepended and the identity fields are exposed as
``{{agent_name}}`` / ``{{agent_email}}`` / ``{{agent_slack}}`` /
``{{agent_mail_name}}`` / ``{{agent_model}}`` substitution vars.
Caller-supplied kwargs always win over identity-derived vars.
"""

from __future__ import annotations

import re
from pathlib import Path

from prefect_orchestration.identity import (
    format_self_block,
    identity_vars,
    load_identity,
)


def render_template(
    agents_dir: Path,
    role: str,
    *,
    rig_path: Path | None = None,
    **vars: object,
) -> str:
    """Read `<agents_dir>/<role>/prompt.md` and substitute `{{var}}` placeholders.

    When an ``identity.toml`` is present alongside the prompt (or
    overridden at ``<rig_path>/.claude/agents/<role>/identity.toml``), a
    ``<self>...</self>`` block is prepended and identity fields are
    available as ``{{agent_name}}`` etc. Caller-passed ``**vars`` win
    over identity-derived vars.
    """
    prompt_path = Path(agents_dir) / role / "prompt.md"
    try:
        template = prompt_path.read_text()
    except FileNotFoundError as e:
        raise FileNotFoundError(
            f"prompt for role {role!r} not found at {prompt_path}"
        ) from e

    identity = load_identity(agents_dir, role, rig_path=rig_path)
    self_block = format_self_block(identity) if identity is not None else ""
    composed = self_block + template

    # Identity-derived vars merge BEHIND caller-supplied vars (caller wins).
    merged_vars: dict[str, object] = {**identity_vars(identity), **vars}
    # `rig_path` is a keyword-only parameter so identity overlay can find
    # `<rig>/.claude/agents/<role>/identity.toml`. But callers commonly do
    # `render_template(..., **ctx)` where ctx already contains `rig_path`
    # — Python binds it to the named param, removing it from `**vars`. We
    # echo it back into merged_vars so prompts can still reference
    # `{{rig_path}}` without callers needing to pass it twice.
    if rig_path is not None and "rig_path" not in merged_vars:
        merged_vars["rig_path"] = str(rig_path)

    def sub(m: re.Match[str]) -> str:
        key = m.group(1).strip()
        if key not in merged_vars:
            raise KeyError(
                f"prompt {role}/prompt.md references {{{{{key}}}}} but it wasn't provided"
            )
        return str(merged_vars[key])

    return re.sub(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", sub, composed)
