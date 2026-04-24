"""Generic `{{var}}` template renderer for prompt files.

Lives in core because every formula needs it, but the `prompts_dir` is
caller-supplied — core ships no prompts of its own.
"""

from __future__ import annotations

import re
from pathlib import Path


def render_template(prompts_dir: Path, name: str, **vars: object) -> str:
    """Read `<prompts_dir>/<name>.md` and substitute `{{var}}` placeholders."""
    template = (Path(prompts_dir) / f"{name}.md").read_text()

    def sub(m: re.Match[str]) -> str:
        key = m.group(1).strip()
        if key not in vars:
            raise KeyError(
                f"prompt {name}.md references {{{{{key}}}}} but it wasn't provided"
            )
        return str(vars[key])

    return re.sub(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", sub, template)
