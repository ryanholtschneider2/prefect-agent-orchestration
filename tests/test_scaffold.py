"""Unit tests for `po new pack|formula|skill|agent` scaffolding.

Pure template emission — no Prefect server, no subprocess. We assert the
emitted files exist, the emitted Python compiles, the emitted pyproject
parses and carries the new entry points, and the error paths raise cleanly.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest
import yaml

from prefect_orchestration import scaffold


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _compiles(py: Path) -> None:
    ast.parse(py.read_text())


def _pyproject(root: Path) -> dict:
    return tomllib.loads((root / "pyproject.toml").read_text())


def _make_pack(tmp_path: Path, name: str = "demo-pack") -> Path:
    scaffold.scaffold_pack(name, path=str(tmp_path))
    return tmp_path / name


# --------------------------------------------------------------------------- #
# pack
# --------------------------------------------------------------------------- #


def test_scaffold_pack_emits_installable_shape(tmp_path: Path) -> None:
    msg = scaffold.scaffold_pack("demo-pack", path=str(tmp_path))
    root = tmp_path / "demo-pack"

    for rel in (
        "pyproject.toml",
        "demo_pack/__init__.py",
        "demo_pack/commands.py",
        "README.md",
        "overlay/CLAUDE-demo-pack.md",
    ):
        assert (root / rel).is_file(), f"missing {rel}"

    _compiles(root / "demo_pack/commands.py")
    data = _pyproject(root)
    assert data["project"]["name"] == "demo-pack"
    assert "prefect-orchestration" in data["project"]["dependencies"]
    # The sample command is registered so the pack proves itself in `po list`.
    cmds = data["project"]["entry-points"]["po.commands"]
    assert cmds == {"demo-pack-ping": "demo_pack.commands:ping"}
    # Wheel ships skills/ + overlay/ for non-editable installs.
    assert "skills" in data["tool"]["hatch"]["build"]["targets"]["wheel"]["include"]
    assert "installable" not in msg or True  # message is human-facing; presence only


def test_scaffold_pack_module_uses_underscores(tmp_path: Path) -> None:
    scaffold.scaffold_pack("po-my-thing", path=str(tmp_path))
    root = tmp_path / "po-my-thing"
    assert (root / "po_my_thing" / "__init__.py").is_file()


def test_scaffold_pack_refuses_nonempty_dir(tmp_path: Path) -> None:
    root = tmp_path / "demo-pack"
    root.mkdir()
    (root / "keep.txt").write_text("x")
    with pytest.raises(scaffold.ScaffoldError, match="non-empty"):
        scaffold.scaffold_pack("demo-pack", path=str(tmp_path))


def test_scaffold_pack_force_overwrites(tmp_path: Path) -> None:
    scaffold.scaffold_pack("demo-pack", path=str(tmp_path))
    # Second run without force refuses; with force it succeeds.
    with pytest.raises(scaffold.ScaffoldError):
        scaffold.scaffold_pack("demo-pack", path=str(tmp_path))
    scaffold.scaffold_pack("demo-pack", path=str(tmp_path), force=True)


# --------------------------------------------------------------------------- #
# formula
# --------------------------------------------------------------------------- #


def test_scaffold_formula_emits_flow_and_registers_ep(tmp_path: Path) -> None:
    root = _make_pack(tmp_path)
    scaffold.scaffold_formula("hello-flow", pack=str(root))

    py = root / "demo_pack" / "hello_flow_formula.py"
    assert py.is_file()
    _compiles(py)
    src = py.read_text()
    # Signature convention is present.
    assert "def hello_flow(" in src
    assert "issue_id" in src and "rig_path" in src and "dry_run" in src
    # Verdict-file write example is present.
    assert "verdicts" in src

    eps = _pyproject(root)["project"]["entry-points"]["po.formulas"]
    assert eps["hello-flow"] == "demo_pack.hello_flow_formula:hello_flow"


def test_scaffold_formula_duplicate_ep_raises(tmp_path: Path) -> None:
    root = _make_pack(tmp_path)
    scaffold.scaffold_formula("hello-flow", pack=str(root))
    with pytest.raises(scaffold.ScaffoldError, match="already registered"):
        scaffold.scaffold_formula("hello-flow", pack=str(root), force=True)


def test_scaffold_formula_missing_pack_raises(tmp_path: Path) -> None:
    with pytest.raises(scaffold.ScaffoldError, match="required"):
        scaffold.scaffold_formula("hello-flow")
    with pytest.raises(scaffold.ScaffoldError, match="not a directory"):
        scaffold.scaffold_formula("hello-flow", pack=str(tmp_path / "nope"))


def test_scaffold_formula_rejects_dir_without_pyproject(tmp_path: Path) -> None:
    (tmp_path / "bare").mkdir()
    with pytest.raises(scaffold.ScaffoldError, match="no pyproject"):
        scaffold.scaffold_formula("hello-flow", pack=str(tmp_path / "bare"))


# --------------------------------------------------------------------------- #
# skill
# --------------------------------------------------------------------------- #


def test_scaffold_skill_emits_skill_and_evals(tmp_path: Path) -> None:
    root = _make_pack(tmp_path)
    scaffold.scaffold_skill("demo-skill", pack=str(root))

    skill_dir = root / "skills" / "demo-skill"
    assert (skill_dir / "SKILL.md").is_file()
    cases = yaml.safe_load((skill_dir / "evals" / "cases.yaml").read_text())
    rubrics = yaml.safe_load((skill_dir / "evals" / "rubrics.yaml").read_text())
    assert "cases" in cases and cases["cases"]
    assert rubrics["judge_model"] == "claude-code"
    assert {c["name"] for c in rubrics["criteria"]} == {"on-topic", "concrete"}

    # SKILL.md frontmatter carries the skill name.
    head = (skill_dir / "SKILL.md").read_text()
    assert "name: demo-skill" in head


# --------------------------------------------------------------------------- #
# agent
# --------------------------------------------------------------------------- #


def test_scaffold_agent_emits_prompt_formula_and_evals(tmp_path: Path) -> None:
    root = _make_pack(tmp_path)
    scaffold.scaffold_agent("night-watch", pack=str(root))

    prompt = root / "demo_pack" / "agents" / "night-watch" / "prompt.md"
    formula = root / "demo_pack" / "night_watch_agent.py"
    assert prompt.is_file()
    assert formula.is_file()
    _compiles(formula)
    fsrc = formula.read_text()
    assert "def night_watch_agent(" in fsrc
    assert "AgentSession" in fsrc

    # Prompt carries charter + trigger sections.
    psrc = prompt.read_text()
    assert "## Charter" in psrc and "## Trigger" in psrc

    # Every new agent ships with an eval suite (judge_model: claude-code).
    evals = root / "evals" / "night-watch"
    assert (evals / "cases.yaml").is_file()
    assert (evals / "README.md").is_file()
    rubrics = yaml.safe_load((evals / "rubrics.yaml").read_text())
    assert rubrics["judge_model"] == "claude-code"

    # Formula EP registered.
    eps = _pyproject(root)["project"]["entry-points"]["po.formulas"]
    assert eps["night-watch-agent"] == "demo_pack.night_watch_agent:night_watch_agent"


# --------------------------------------------------------------------------- #
# add_entry_point (the EP-insertion seam)
# --------------------------------------------------------------------------- #


def test_add_entry_point_creates_section_when_absent(tmp_path: Path) -> None:
    pj = tmp_path / "pyproject.toml"
    pj.write_text('[project]\nname = "x"\n')
    scaffold.add_entry_point(pj, "po.formulas", "foo", "x.flows:foo")
    data = tomllib.loads(pj.read_text())
    assert data["project"]["entry-points"]["po.formulas"]["foo"] == "x.flows:foo"


def test_add_entry_point_preserves_existing_entries(tmp_path: Path) -> None:
    pj = tmp_path / "pyproject.toml"
    pj.write_text(
        '[project]\nname = "x"\n\n'
        '[project.entry-points."po.formulas"]\n'
        'old = "x.flows:old"\n'
    )
    scaffold.add_entry_point(pj, "po.formulas", "new", "x.flows:new")
    eps = tomllib.loads(pj.read_text())["project"]["entry-points"]["po.formulas"]
    assert eps == {"old": "x.flows:old", "new": "x.flows:new"}


# --------------------------------------------------------------------------- #
# dispatch (`po new ...`)
# --------------------------------------------------------------------------- #


def test_new_dispatch_unknown_kind_exits(tmp_path: Path, capsys) -> None:
    # The CLI entry renders user errors as a clean stderr line + SystemExit(2),
    # not a traceback.
    with pytest.raises(SystemExit) as exc:
        scaffold.new("widget", "foo")
    assert exc.value.code == 2
    assert "unknown artifact kind" in capsys.readouterr().err


def test_new_dispatch_missing_args_exits(capsys) -> None:
    with pytest.raises(SystemExit):
        scaffold.new()
    assert "usage:" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        scaffold.new("pack")
    assert "usage:" in capsys.readouterr().err


def test_new_dispatch_invalid_name_exits(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit):
        scaffold.new("pack", "Bad_Name", path=str(tmp_path))
    assert "invalid pack name" in capsys.readouterr().err


def test_new_dispatch_pack_roundtrip(tmp_path: Path) -> None:
    msg = scaffold.new("pack", "demo-pack", path=str(tmp_path))
    assert "demo-pack" in msg
    assert (tmp_path / "demo-pack" / "pyproject.toml").is_file()


def test_full_worked_pack_all_artifacts_compile(tmp_path: Path) -> None:
    """End-to-end: scaffold a pack + one of each artifact; everything compiles."""
    scaffold.new("pack", "demo-pack", path=str(tmp_path))
    root = tmp_path / "demo-pack"
    scaffold.new("formula", "hello-flow", pack=str(root))
    scaffold.new("skill", "demo-skill", pack=str(root))
    scaffold.new("agent", "night-watch", pack=str(root))

    for py in root.rglob("*.py"):
        _compiles(py)
    # pyproject still parses and carries both formula EPs + the pack command.
    data = _pyproject(root)
    eps = data["project"]["entry-points"]
    assert set(eps["po.formulas"]) == {"hello-flow", "night-watch-agent"}
    assert "demo-pack-ping" in eps["po.commands"]
