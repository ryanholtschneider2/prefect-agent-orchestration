"""Generic `{{var}}` template renderer for agent prompt files.

Lives in core because every formula needs it, but the `agents_dir` is
caller-supplied — core ships no prompts of its own.

Layout (per `engdocs/principles.md` — "Prompt authoring convention"):

    <pack>/po_formulas/agents/
      triager/
        prompt.md
      builder/
        prompt.md
      ...

`render_template(agents_dir, "triager", ...)` reads
`<agents_dir>/triager/prompt.md`. Plain markdown — no Jinja, no fragments.
"""

from __future__ import annotations

import re
from pathlib import Path


def render_template(agents_dir: Path, role: str, **vars: object) -> str:
    """Read `<agents_dir>/<role>/prompt.md` and substitute `{{var}}` placeholders."""
    prompt_path = Path(agents_dir) / role / "prompt.md"
    try:
        template = prompt_path.read_text()
    except FileNotFoundError as e:
        raise FileNotFoundError(
            f"prompt for role {role!r} not found at {prompt_path}"
        ) from e

    def sub(m: re.Match[str]) -> str:
        key = m.group(1).strip()
        if key not in vars:
            raise KeyError(
                f"prompt {role}/prompt.md references {{{{{key}}}}} but it wasn't provided"
            )
        return str(vars[key])

    return re.sub(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", sub, template)
