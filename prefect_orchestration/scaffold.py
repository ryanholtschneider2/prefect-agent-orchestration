"""`po new pack|formula|skill|agent` — turnkey scaffolding for PO artifacts.

Shipped as a single `po.commands` entry (`po new ...`), pure transport: it
emits correctly-shaped files from in-code templates so a new pack / formula /
skill / agent comes out in the standard shape without hand-rolling. No Prefect
overhead — scaffolding is a one-shot utility op, not a flow (principles §4).

    po new pack <name> [--path DIR]
    po new formula <name> --pack <pack-root>
    po new skill <name> --pack <pack-root>
    po new agent <name> --pack <pack-root>

`--pack` is a filesystem path to an existing pack root (the dir holding the
pack's `pyproject.toml`). `formula`/`agent` append their entry point to that
pyproject; `skill`/`agent` also drop an eval suite so "every new agent ships
with evals" is automatic.

Templates live as `string.Template` ($-substitution) constants in this module
rather than packaged files — emission stays deterministic and unit-testable,
and there's nothing extra to force-include in the wheel.

See `engdocs/creating-artifacts.md` for the worked shapes and when to use each.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path
from string import Template

# Artifact kinds accepted as the first positional to `po new`.
KINDS = ("pack", "formula", "skill", "agent")


class ScaffoldError(Exception):
    """Raised on any user-facing scaffolding error (bad args, collisions)."""


# --------------------------------------------------------------------------- #
# Name helpers
# --------------------------------------------------------------------------- #

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$")


def _validate_name(name: str, *, label: str = "name") -> str:
    """Lower-kebab artifact names only — they become dist names / EP keys / dirs."""
    if not name or not _NAME_RE.match(name):
        raise ScaffoldError(
            f"invalid {label} {name!r}: use lower-case letters, digits and "
            "hyphens (e.g. 'my-thing'), not starting/ending with a hyphen"
        )
    return name


def _module_name(dist_name: str) -> str:
    """Python module name for a distribution name (`po-stripe` -> `po_stripe`)."""
    return dist_name.replace("-", "_")


def _snake(name: str) -> str:
    """`my-formula` -> `my_formula` for Python identifiers."""
    return name.replace("-", "_")


# --------------------------------------------------------------------------- #
# Filesystem + pyproject helpers
# --------------------------------------------------------------------------- #


def _write(path: Path, content: str, *, force: bool) -> Path:
    """Write `content` to `path`, creating parents. Refuse to clobber unless force."""
    if path.exists() and not force:
        raise ScaffoldError(
            f"refusing to overwrite existing file: {path} (pass --force)"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _resolve_pack_root(pack: str | None) -> Path:
    """Resolve the `--pack` arg to a pack root dir (must hold a pyproject.toml)."""
    if not pack:
        raise ScaffoldError("--pack <pack-root> is required for this artifact")
    root = Path(pack).expanduser().resolve()
    if not root.is_dir():
        raise ScaffoldError(f"--pack path is not a directory: {root}")
    if not (root / "pyproject.toml").is_file():
        raise ScaffoldError(f"--pack root has no pyproject.toml: {root}")
    return root


def _pack_dist_name(root: Path) -> str:
    """Read `[project].name` from a pack's pyproject.toml."""
    data = tomllib.loads((root / "pyproject.toml").read_text())
    name = data.get("project", {}).get("name")
    if not name:
        raise ScaffoldError(f"{root}/pyproject.toml has no [project].name")
    return str(name)


def _pack_module_dir(root: Path) -> Path:
    """Locate the importable package dir inside a pack root.

    Prefers the module derived from the dist name; falls back to the sole
    directory containing an `__init__.py` (excluding tests).
    """
    module = _module_name(_pack_dist_name(root))
    candidate = root / module
    if (candidate / "__init__.py").is_file():
        return candidate
    pkgs = [
        d
        for d in sorted(root.iterdir())
        if d.is_dir() and d.name != "tests" and (d / "__init__.py").is_file()
    ]
    if len(pkgs) == 1:
        return pkgs[0]
    raise ScaffoldError(
        f"could not locate the package dir under {root} (expected {module}/__init__.py)"
    )


def _ep_line(group: str, key: str, target: str) -> str:
    return f'{key} = "{target}"'


def add_entry_point(pyproject: Path, group: str, key: str, target: str) -> None:
    """Insert `key = "target"` under `[project.entry-points."<group>"]`.

    Inserts under an existing header (preserving the rest of the file) or
    appends a fresh section when the group is absent. Refuses a duplicate key.
    Text-surgical on purpose: a TOML round-trip risks dropping comments and
    reordering entry-point blocks, which has bitten this repo before.
    """
    text = pyproject.read_text()
    header = f'[project.entry-points."{group}"]'
    new_line = _ep_line(group, key, target)

    # Duplicate-key guard (parse the structured data, not the raw text).
    data = tomllib.loads(text)
    existing = data.get("project", {}).get("entry-points", {}).get(group, {})
    if key in existing:
        raise ScaffoldError(
            f'entry point {key!r} already registered under "{group}" in {pyproject}'
        )

    lines = text.splitlines()
    if header in lines:
        idx = lines.index(header)
        # Insert right after the header so the new entry leads the block.
        lines.insert(idx + 1, new_line)
        pyproject.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""))
        return

    # Append a fresh section.
    block = f"\n{header}\n{new_line}\n"
    sep = "" if text.endswith("\n") else "\n"
    pyproject.write_text(text + sep + block)


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #

PACK_PYPROJECT = Template(
    """[project]
name = "$dist"
version = "0.1.0"
description = "po pack: $dist"
requires-python = ">=3.11"
dependencies = [
    "prefect-orchestration",
]

# Pack-shipped utility ops dispatched as `po <command>` (NOT `po run`).
# Add more with `po new ...` or by hand.
[project.entry-points."po.commands"]
$name-ping = "$module.commands:ping"

# @flow pipelines dispatched as `po run <formula>`. Add with
# `po new formula <name> --pack .` from this dir.
# [project.entry-points."po.formulas"]

# Health checks surfaced by `po doctor`.
# [project.entry-points."po.doctor_checks"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["$module"]
# Ship skills/ and overlay/ inside the wheel so non-editable installs find
# them (the pack-convention wheel-layout probe looks at <dist-root>/{skills,overlay}/).
include = ["skills", "overlay"]
"""
)

PACK_INIT = Template('"""$dist — a po pack scaffolded by `po new pack`."""\n')

PACK_COMMANDS = Template(
    '''"""Pack-shipped `po.commands` utility ops for $dist.

These dispatch as `po <command>` (NOT `po run`) and skip Prefect overhead.
Signature convention: plain callables, `print()` to stdout, `raise SystemExit(2)`
on error. Register each in pyproject under [project.entry-points."po.commands"].
"""

from __future__ import annotations


def ping() -> None:
    """Smoke command — prove the pack is installed and discoverable."""
    print("$dist: pong")
'''
)

PACK_README = Template(
    """# $dist

A [po](https://github.com/) pack scaffolded with `po new pack`.

## Install (editable, for development)

```bash
po packs install --editable .
po list            # shows `$name-ping` (and any formulas you add)
po $name-ping      # -> "$dist: pong"
```

## Add to it

```bash
po new formula my-flow --pack .     # adds a @flow under po.formulas
po new skill my-skill --pack .      # adds skills/my-skill/SKILL.md + evals/
po new agent my-agent --pack .      # adds an agent prompt + cron formula + evals
```

## Layout

```
$dist/
  pyproject.toml          # [project.entry-points."po.*"] groups
  $module/
    __init__.py
    commands.py           # po.commands utility ops
  overlay/
    CLAUDE-$name.md        # ~150-word discovery summary copied into rigs
  skills/                 # SKILL.md + evals/ (po new skill)
```
"""
)

PACK_OVERLAY = Template(
    """# $name

**What it provides:** <one-liner — what this pack adds to a rig>.

**When to use:**
- <scenario where an agent should reach for this pack>

**Key verbs:** `po $name-ping`, ...
**Key paths:** `$module/commands.py`, `skills/`, `overlay/`

**Skip if:** <when this pack is not relevant>

**Read more:** `po show $name-ping`, `$dist/README.md`
"""
)

FORMULA_PY = Template(
    '''"""`$name` formula — a @flow dispatched via `po run $name`.

Scaffolded by `po new formula`. Follows the PO formula signature convention:
`(issue_id, rig, rig_path, *, parent_bead=None, dry_run=False)`. Verdicts flow
to the orchestrator as files under `$$RUN_DIR/verdicts/<step>.json` (or as bd
metadata on dolt rigs — see engdocs/verdict-channel-backends.md). Replace the
body with the real pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prefect import flow


@flow(name="$name", flow_run_name="{issue_id}", log_prints=True)
def $snake(
    issue_id: str,
    rig: str,
    rig_path: str,
    *,
    parent_bead: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """One-line summary of what `$name` does.

    Args:
        issue_id: the seed bead this run implements.
        rig: rig slug (usually the rig-path basename).
        rig_path: absolute path to the repo where code lives.
        parent_bead: parent bead id when dispatched as a graph node.
        dry_run: skip side effects; emit a stubbed verdict.
    """
    run_dir = Path(rig_path) / ".planning" / "$name" / issue_id
    verdicts = run_dir / "verdicts"
    verdicts.mkdir(parents=True, exist_ok=True)

    if dry_run:
        status = "pass"
        summary = "dry-run: no work performed"
    else:
        # TODO: do the real work here (spawn an AgentSession, run a step, etc.).
        status = "pass"
        summary = "scaffolded formula — replace this body"

    # Verdict-file write example — orchestrator-readable pass/fail artifact.
    (verdicts / "$snake.json").write_text(
        json.dumps({"status": status, "summary": summary}, indent=2)
    )
    return {"status": status, "issue_id": issue_id, "run_dir": str(run_dir)}
'''
)

SKILL_MD = Template(
    """---
name: $name
description: <1-2 sentence summary of when an agent should load this skill>.
---

# $name skill

Replace this with the canonical how-to for $name. Keep it operational:
the rules that matter, the canonical commands, the footguns.

## Rules that matter

1. <rule one>
2. <rule two>

## Canonical recipes

```bash
# show the real commands here
```

## Evals

This skill ships an `evals/` suite (`cases.yaml` + `rubrics.yaml`). Run it with:

```bash
po run skill-evals --pack $dist --skill $name --dry-run   # CI-safe smoke
po run skill-evals --pack $dist --skill $name             # real judge
```
"""
)

SKILL_CASES = Template(
    """# Eval cases for the `$name` skill.
#
#   po run skill-evals --pack $dist --skill $name --dry-run   # CI-safe
#   po run skill-evals --pack $dist --skill $name             # real judge
#   po run skill-evals --pack $dist --skill $name --tier smoke

cases:
  - name: smoke-basic
    tier: smoke
    prompt: |
      Ask a representative question this skill should answer well.
    evaluators: [on-topic, concrete]

  - name: regression-edge-case
    tier: regression
    prompt: |
      Ask about a known footgun or edge case the skill must handle.
    evaluators: [on-topic, concrete]
    pass_threshold: 0.7
"""
)

SKILL_RUBRICS = Template(
    """# Rubric criteria for the `$name` skill. Each becomes an LLMJudge; cases
# reference criteria by name in their `evaluators:` list.

judge_model: claude-code
pass_threshold: 0.75

criteria:
  - name: on-topic
    rubric: |
      Did the response substantively address the question using this skill's
      conventions (not generic boilerplate)?
    scoring_guide: |
      1.0 = on-topic and specific. 0.5 = on-topic but thin. 0.0 = off-topic.
  - name: concrete
    rubric: |
      Does the response include a concrete, runnable command or a specific,
      actionable answer rather than vague description?
    scoring_guide: |
      1.0 = concrete and copy-pasteable. 0.5 = partially. 0.0 = vague.
"""
)

AGENT_PROMPT = Template(
    """# $titlecase

You are **$name**, an operating agent in this rig.

## Charter

<One paragraph: what this agent is responsible for, the outcome it owns, and
the bar for "good". Be specific — this is the agent's whole job.>

## Trigger

<When does this agent run? A cron cadence, a bd event (post-close hook), or a
mail/heartbeat. The `$snake-agent` formula wires the actual trigger.>

## How you work

1. Read the current state (`bd ready`, `bd list`, run-dir artifacts).
2. <step the agent takes>
3. Escalate with `bd human <issue> --question="..."` when a human decision is
   required. Don't guess on irreversible or outward-facing actions.

## Done

<What "done" looks like for one turn, and what you leave behind (a bead, an
artifact, a mail) so the next turn — or a human — can verify it.>
"""
)

AGENT_FORMULA_PY = Template(
    '''"""`$name-agent` — runs the `$name` operating agent via AgentSession.

Scaffolded by `po new agent`. This @flow is the trigger surface: register a
cron/interval/event deployment for it (see po_formulas deployments / `po run
$name-agent --at ...`). Each run renders `agents/$name/prompt.md` and takes one
agent turn. Replace the body with the real loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from prefect import flow

from prefect_orchestration.agent_session import AgentSession
from prefect_orchestration.backend_select import select_default_backend
from prefect_orchestration.templates import render_template

_AGENTS_DIR = Path(__file__).parent / "agents"


def _make_backend(role: str, issue: str):
    """Instantiate the selected backend factory (tmux needs issue+role)."""
    factory = select_default_backend()
    try:
        return factory(issue=issue, role=role)
    except TypeError:
        return factory()


@flow(name="$name-agent", flow_run_name="$name", log_prints=True)
def ${snake}_agent(
    rig_path: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run one turn of the `$name` agent against `rig_path`."""
    rig = Path(rig_path).expanduser().resolve()
    prompt = render_template(_AGENTS_DIR, "$name", rig_path=rig)

    if dry_run:
        return {"status": "dry-run", "agent": "$name", "prompt_chars": len(prompt)}

    session = AgentSession(
        role="$name",
        repo_path=rig,
        backend=_make_backend("$name", "$name-agent"),
    )
    reply = session.prompt(prompt)
    return {"status": "ok", "agent": "$name", "reply_chars": len(reply)}
'''
)

AGENT_EVAL_CASES = Template(
    """# aeval cases for the `$name` agent.
#
# Drive the agent-under-test on the tmux backend (PO_BACKEND=tmux) so the eval
# exercises the same lurkable runtime production uses. Judge via the local
# Claude Agent SDK over OAuth (judge_model: claude-code) — never an API key.
#
# Seed from real transcripts: every time a human had to step in becomes a case
# so the same correction never has to be given twice.

cases:
  - name: smoke-charter
    tier: smoke
    prompt: |
      A representative first message this agent should handle on its charter.
    evaluators: [acts-on-charter, escalates-appropriately]

  - name: regression-escalation
    tier: regression
    prompt: |
      A scenario where the correct move is to escalate (bd human) rather than
      act — make sure the agent doesn't guess on an irreversible action.
    evaluators: [escalates-appropriately]
    pass_threshold: 0.7
"""
)

AGENT_EVAL_RUBRICS = Template(
    """# Rubric criteria for the `$name` agent. Judge with the local Claude Agent
# SDK over OAuth (the Claude.ai subscription), not an API key.

judge_model: claude-code
pass_threshold: 0.75

criteria:
  - name: acts-on-charter
    rubric: |
      Did the agent take the action its charter (agents/$name/prompt.md) calls
      for, with the right scope — not over-reaching, not stopping short?
    scoring_guide: |
      1.0 = correct action on charter. 0.5 = partial. 0.0 = wrong/no action.
  - name: escalates-appropriately
    rubric: |
      When a human decision was required (irreversible / outward-facing /
      ambiguous), did the agent escalate via `bd human` instead of guessing?
    scoring_guide: |
      1.0 = escalated when it should, acted when it could. 0.5 = one wrong call.
      0.0 = guessed on something it should have escalated.
"""
)

AGENT_EVAL_README = Template(
    """# `$name` agent evals

Eval suite for the `$name` operating agent (scaffolded by `po new agent`).
Every PO agent ships with evals — this is the durable form of the lessons the
agent learns when a human steps in.

## Run

```bash
# from agent-evals-best-practices, with the aeval package:
PO_BACKEND=tmux aeval run --suite . --judge-model claude-code
```

- **Backend:** drive the agent-under-test on the tmux backend (`PO_BACKEND=tmux`)
  so the eval matches the lurkable production runtime; attach mid-turn to watch.
- **Judge:** `claude-code` over OAuth (`~/.claude/.credentials.json`). Do NOT set
  `ANTHROPIC_API_KEY` for eval runs — the SDK spawns the Claude CLI and the key
  would override OAuth.
- **Cases come from real-world results.** Seed `cases.yaml` from concrete
  scenarios, then grow it from production transcripts and every human escalation.

See `~/Desktop/Code/personal/agent-evals-best-practices/` for the runner and the
case/rubric schema.
"""
)


# --------------------------------------------------------------------------- #
# Scaffolders
# --------------------------------------------------------------------------- #


def scaffold_pack(name: str, *, path: str | None = None, force: bool = False) -> str:
    """Emit a minimal installable po pack at `<path>/<name>/`."""
    _validate_name(name, label="pack name")
    module = _module_name(name)
    base = Path(path).expanduser().resolve() if path else Path.cwd()
    root = base / name
    if root.exists() and any(root.iterdir()) and not force:
        raise ScaffoldError(
            f"refusing to scaffold into non-empty dir: {root} (pass --force)"
        )

    subs = {"dist": name, "name": name, "module": module}
    written = [
        _write(root / "pyproject.toml", PACK_PYPROJECT.substitute(subs), force=force),
        _write(root / module / "__init__.py", PACK_INIT.substitute(subs), force=force),
        _write(
            root / module / "commands.py", PACK_COMMANDS.substitute(subs), force=force
        ),
        _write(root / "README.md", PACK_README.substitute(subs), force=force),
        _write(
            root / "overlay" / f"CLAUDE-{name}.md",
            PACK_OVERLAY.substitute(subs),
            force=force,
        ),
    ]
    return (
        f"scaffolded pack {name!r} at {root} ({len(written)} files). "
        f"Install: `po packs install --editable {root}` then `po list`."
    )


def scaffold_formula(name: str, *, pack: str | None = None, force: bool = False) -> str:
    """Emit a @flow formula stub into an existing pack and register its EP."""
    _validate_name(name, label="formula name")
    root = _resolve_pack_root(pack)
    module_dir = _pack_module_dir(root)
    module = module_dir.name
    snake = _snake(name)

    subs = {"name": name, "snake": snake, "module": module}
    py = _write(
        module_dir / f"{snake}_formula.py", FORMULA_PY.substitute(subs), force=force
    )
    add_entry_point(
        root / "pyproject.toml",
        "po.formulas",
        name,
        f"{module}.{snake}_formula:{snake}",
    )
    return (
        f"scaffolded formula {name!r} at {py} and registered it under "
        f'[project.entry-points."po.formulas"]. Run `po packs update` then '
        f"`po run {name} --issue-id <id> --rig <r> --rig-path <p>`."
    )


def scaffold_skill(name: str, *, pack: str | None = None, force: bool = False) -> str:
    """Emit SKILL.md + an evals/ sibling into an existing pack's skills/ dir."""
    _validate_name(name, label="skill name")
    root = _resolve_pack_root(pack)
    dist = _pack_dist_name(root)
    skill_dir = root / "skills" / name

    subs = {"name": name, "dist": dist}
    written = [
        _write(skill_dir / "SKILL.md", SKILL_MD.substitute(subs), force=force),
        _write(
            skill_dir / "evals" / "cases.yaml",
            SKILL_CASES.substitute(subs),
            force=force,
        ),
        _write(
            skill_dir / "evals" / "rubrics.yaml",
            SKILL_RUBRICS.substitute(subs),
            force=force,
        ),
    ]
    return (
        f"scaffolded skill {name!r} at {skill_dir} ({len(written)} files). "
        f"Smoke its evals: `po run skill-evals --pack {dist} --skill {name} --dry-run`."
    )


def scaffold_agent(name: str, *, pack: str | None = None, force: bool = False) -> str:
    """Emit an operating-agent: prompt + cron/event formula + eval suite."""
    _validate_name(name, label="agent name")
    root = _resolve_pack_root(pack)
    module_dir = _pack_module_dir(root)
    module = module_dir.name
    snake = _snake(name)
    titlecase = name.replace("-", " ").title()

    subs = {
        "name": name,
        "snake": snake,
        "module": module,
        "dist": _pack_dist_name(root),
        "titlecase": titlecase,
    }
    written = [
        _write(
            module_dir / "agents" / name / "prompt.md",
            AGENT_PROMPT.substitute(subs),
            force=force,
        ),
        _write(
            module_dir / f"{snake}_agent.py",
            AGENT_FORMULA_PY.substitute(subs),
            force=force,
        ),
        _write(
            root / "evals" / name / "cases.yaml",
            AGENT_EVAL_CASES.substitute(subs),
            force=force,
        ),
        _write(
            root / "evals" / name / "rubrics.yaml",
            AGENT_EVAL_RUBRICS.substitute(subs),
            force=force,
        ),
        _write(
            root / "evals" / name / "README.md",
            AGENT_EVAL_README.substitute(subs),
            force=force,
        ),
    ]
    add_entry_point(
        root / "pyproject.toml",
        "po.formulas",
        f"{name}-agent",
        f"{module}.{snake}_agent:{snake}_agent",
    )
    return (
        f"scaffolded agent {name!r} ({len(written)} files): prompt + "
        f"`{name}-agent` cron/event formula + aeval suite. "
        f"Every new agent ships with evals — see evals/{name}/README.md."
    )


# --------------------------------------------------------------------------- #
# `po.commands` entry point
# --------------------------------------------------------------------------- #

_DISPATCH = {
    "pack": lambda name, pack, path, force: scaffold_pack(name, path=path, force=force),
    "formula": lambda name, pack, path, force: scaffold_formula(
        name, pack=pack, force=force
    ),
    "skill": lambda name, pack, path, force: scaffold_skill(
        name, pack=pack, force=force
    ),
    "agent": lambda name, pack, path, force: scaffold_agent(
        name, pack=pack, force=force
    ),
}


def new(
    kind: str | None = None,
    name: str | None = None,
    *,
    pack: str | None = None,
    path: str | None = None,
    force: bool = False,
) -> str:
    """Scaffold a PO artifact. `po new <kind> <name> [--pack DIR] [--path DIR]`.

    kind: one of pack | formula | skill | agent.
    name: lower-kebab artifact name.
    --pack: existing pack root (required for formula/skill/agent).
    --path: parent dir for a new pack (default: cwd).
    --force: overwrite existing files.
    """
    try:
        if kind is None or name is None:
            raise ScaffoldError(
                "usage: po new <pack|formula|skill|agent> <name> "
                "[--pack <pack-root>] [--path <dir>] [--force]"
            )
        if kind not in _DISPATCH:
            raise ScaffoldError(
                f"unknown artifact kind {kind!r}; choose one of {', '.join(KINDS)}"
            )
        return _DISPATCH[kind](name, pack, path, bool(force))
    except ScaffoldError as exc:
        # Render user errors as a clean one-liner, not a traceback (po.commands
        # convention: print to stderr, exit non-zero).
        print(f"po new: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
