"""Unit tests for pack_overlay: overlay + skills materialization."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from prefect_orchestration.pack_overlay import (
    Pack,
    apply_overlay,
    apply_skills,
    materialize_packs,
)


def _make_pack(
    tmp_path: Path,
    name: str = "po-stripe",
    *,
    module: str = "po_stripe",
    overlay_files: dict[str, str] | None = None,
    skills: dict[str, dict[str, str]] | None = None,
    role_overlays: dict[str, dict[str, str]] | None = None,
    embed_in_module: bool = False,
) -> Pack:
    """Build a fake pack on disk and return a Pack handle.

    ``overlay_files``/``skills``/``role_overlays`` map relative path → contents.
    With ``embed_in_module=True`` the overlay/skills dirs live inside the
    importable package (wheel-style); otherwise they sit at dist root
    (editable-install style).
    """
    pack_root = tmp_path / name
    pack_root.mkdir(parents=True, exist_ok=True)
    module_root = pack_root / module
    module_root.mkdir()
    (module_root / "__init__.py").write_text("")

    overlay_base = module_root if embed_in_module else pack_root
    skills_base = module_root if embed_in_module else pack_root

    for rel, content in (overlay_files or {}).items():
        target = overlay_base / "overlay" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    for skill_name, files in (skills or {}).items():
        for rel, content in files.items():
            target = skills_base / "skills" / skill_name / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

    for role, files in (role_overlays or {}).items():
        for rel, content in files.items():
            target = module_root / "agents" / role / "overlay" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

    return Pack(name=name, root=pack_root, module_root=module_root)


def test_apply_overlay_copies_files(tmp_path: Path) -> None:
    pack = _make_pack(
        tmp_path / "src",
        overlay_files={
            "CLAUDE.md": "stripe rules",
            "scripts/run.sh": "#!/bin/sh\necho hi\n",
        },
    )
    cwd = tmp_path / "rig"
    cwd.mkdir()

    written = apply_overlay(pack, cwd)

    assert (cwd / "CLAUDE.md").read_text() == "stripe rules"
    assert (cwd / "scripts" / "run.sh").read_text().startswith("#!/bin/sh")
    assert {p.name for p in written} == {"CLAUDE.md", "run.sh"}


def test_apply_overlay_skips_existing(tmp_path: Path) -> None:
    pack = _make_pack(
        tmp_path / "src",
        overlay_files={"CLAUDE.md": "from-pack"},
    )
    cwd = tmp_path / "rig"
    cwd.mkdir()
    (cwd / "CLAUDE.md").write_text("user-authored")

    apply_overlay(pack, cwd)

    assert (cwd / "CLAUDE.md").read_text() == "user-authored"


def test_apply_overlay_role_stacks_on_pack_wide(tmp_path: Path) -> None:
    pack = _make_pack(
        tmp_path / "src",
        overlay_files={"CLAUDE.md": "pack-wide"},
        role_overlays={"builder": {"CLAUDE.md": "builder-specific"}},
    )
    cwd = tmp_path / "rig"
    cwd.mkdir()

    apply_overlay(pack, cwd, role="builder")

    # Role overlay is processed first; pack-wide skips because file now exists.
    assert (cwd / "CLAUDE.md").read_text() == "builder-specific"


def test_apply_overlay_no_role_falls_back_to_pack_wide(tmp_path: Path) -> None:
    pack = _make_pack(
        tmp_path / "src",
        overlay_files={"CLAUDE.md": "pack-wide"},
        role_overlays={"builder": {"CLAUDE.md": "builder-specific"}},
    )
    cwd = tmp_path / "rig"
    cwd.mkdir()

    apply_overlay(pack, cwd, role="critic")

    assert (cwd / "CLAUDE.md").read_text() == "pack-wide"


def test_apply_overlay_preserves_executable_bit(tmp_path: Path) -> None:
    pack_root = tmp_path / "src" / "po-stripe"
    pack_root.mkdir(parents=True)
    module_root = pack_root / "po_stripe"
    module_root.mkdir()
    (module_root / "__init__.py").write_text("")
    script = pack_root / "overlay" / "scripts" / "run.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n")
    os.chmod(script, 0o755)
    pack = Pack(name="po-stripe", root=pack_root, module_root=module_root)

    cwd = tmp_path / "rig"
    cwd.mkdir()
    apply_overlay(pack, cwd)

    target = cwd / "scripts" / "run.sh"
    assert target.exists()
    assert target.stat().st_mode & 0o111  # any execute bit set


def test_apply_skills_lands_at_pack_named_dir(tmp_path: Path) -> None:
    pack = _make_pack(
        tmp_path / "src",
        name="po-stripe",
        skills={"stripe": {"SKILL.md": "stripe skill body"}},
    )
    cwd = tmp_path / "rig"
    cwd.mkdir()

    apply_skills(pack, cwd)

    target = cwd / ".claude" / "skills" / "po-stripe" / "stripe" / "SKILL.md"
    assert target.read_text() == "stripe skill body"


def test_apply_skills_overwrites(tmp_path: Path) -> None:
    pack = _make_pack(
        tmp_path / "src",
        name="po-stripe",
        skills={"stripe": {"SKILL.md": "fresh"}},
    )
    cwd = tmp_path / "rig"
    target = cwd / ".claude" / "skills" / "po-stripe" / "stripe" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("stale")

    apply_skills(pack, cwd)

    assert target.read_text() == "fresh"


def test_apply_skills_copies_sibling_files(tmp_path: Path) -> None:
    pack = _make_pack(
        tmp_path / "src",
        name="po-stripe",
        skills={
            "stripe": {
                "SKILL.md": "body",
                "scripts/helper.sh": "#!/bin/sh\n",
                "examples/charge.py": "import stripe\n",
            }
        },
    )
    cwd = tmp_path / "rig"
    cwd.mkdir()

    apply_skills(pack, cwd)

    base = cwd / ".claude" / "skills" / "po-stripe" / "stripe"
    assert (base / "SKILL.md").exists()
    assert (base / "scripts" / "helper.sh").exists()
    assert (base / "examples" / "charge.py").exists()


def test_apply_skills_leaves_unrelated_skills_alone(tmp_path: Path) -> None:
    pack = _make_pack(
        tmp_path / "src",
        name="po-stripe",
        skills={"stripe": {"SKILL.md": "stripe"}},
    )
    cwd = tmp_path / "rig"
    user_skill = cwd / ".claude" / "skills" / "user-authored" / "thing" / "SKILL.md"
    user_skill.parent.mkdir(parents=True)
    user_skill.write_text("mine")

    apply_skills(pack, cwd)

    assert user_skill.read_text() == "mine"
    assert (cwd / ".claude" / "skills" / "po-stripe" / "stripe" / "SKILL.md").exists()


def test_materialize_packs_opt_out_overlay(tmp_path: Path) -> None:
    pack = _make_pack(
        tmp_path / "src",
        skills={"stripe": {"SKILL.md": "x"}},
        overlay_files={"CLAUDE.md": "x"},
    )
    cwd = tmp_path / "rig"
    cwd.mkdir()

    materialize_packs(cwd, role="builder", overlay=False, skills=True, packs=[pack])

    assert not (cwd / "CLAUDE.md").exists()
    assert (cwd / ".claude" / "skills" / "po-stripe" / "stripe" / "SKILL.md").exists()


def test_materialize_packs_opt_out_skills(tmp_path: Path) -> None:
    pack = _make_pack(
        tmp_path / "src",
        skills={"stripe": {"SKILL.md": "x"}},
        overlay_files={"CLAUDE.md": "x"},
    )
    cwd = tmp_path / "rig"
    cwd.mkdir()

    materialize_packs(cwd, role="builder", overlay=True, skills=False, packs=[pack])

    assert (cwd / "CLAUDE.md").exists()
    assert not (cwd / ".claude" / "skills").exists()


def test_materialize_packs_opt_out_both(tmp_path: Path) -> None:
    pack = _make_pack(
        tmp_path / "src",
        skills={"stripe": {"SKILL.md": "x"}},
        overlay_files={"CLAUDE.md": "x"},
    )
    cwd = tmp_path / "rig"
    cwd.mkdir()

    materialize_packs(cwd, role=None, overlay=False, skills=False, packs=[pack])

    assert list(cwd.iterdir()) == []


def test_materialize_packs_no_dirs_is_noop(tmp_path: Path) -> None:
    pack_root = tmp_path / "empty-pack"
    pack_root.mkdir()
    module_root = pack_root / "po_empty"
    module_root.mkdir()
    (module_root / "__init__.py").write_text("")
    pack = Pack(name="po-empty", root=pack_root, module_root=module_root)
    cwd = tmp_path / "rig"
    cwd.mkdir()

    results = materialize_packs(cwd, role="builder", packs=[pack])

    assert results["po-empty:overlay"] == []
    assert results["po-empty:skills"] == []


def test_embedded_overlay_in_module_root(tmp_path: Path) -> None:
    """Wheel-style packs ship overlay/ inside the importable package."""
    pack = _make_pack(
        tmp_path / "src",
        overlay_files={"CLAUDE.md": "from-module"},
        skills={"stripe": {"SKILL.md": "from-module"}},
        embed_in_module=True,
    )
    cwd = tmp_path / "rig"
    cwd.mkdir()

    apply_overlay(pack, cwd)
    apply_skills(pack, cwd)

    assert (cwd / "CLAUDE.md").read_text() == "from-module"
    assert (
        cwd / ".claude" / "skills" / "po-stripe" / "stripe" / "SKILL.md"
    ).read_text() == "from-module"


@pytest.mark.parametrize(
    "name,expected", [("po-stripe", "po-stripe"), ("po-gmail", "po-gmail")]
)
def test_skill_destination_uses_distribution_name(
    tmp_path: Path, name: str, expected: str
) -> None:
    pack = _make_pack(
        tmp_path / name,
        name=name,
        module=name.replace("-", "_"),
        skills={name.split("-", 1)[1]: {"SKILL.md": "x"}},
    )
    cwd = tmp_path / "rig"
    cwd.mkdir()

    apply_skills(pack, cwd)

    assert (cwd / ".claude" / "skills" / expected).is_dir()
