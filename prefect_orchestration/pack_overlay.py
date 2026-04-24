"""Pack-contributed overlay + skill materialization.

At AgentSession start we walk every installed PO pack and copy two
kinds of pack-shipped content into the agent's working directory:

1. ``<pack>/overlay/**`` — arbitrary files merged into the rig cwd.
   Existing files are never overwritten (filesystem-presence check).
   Per-role overlay (``<pack>/<module>/agents/<role>/overlay/**``)
   stacks on top with the same skip-existing semantics, so role-
   specific content lands first and effectively wins on conflict.

2. ``<pack>/skills/<name>/SKILL.md`` (and siblings) — copied to
   ``<rig>/.claude/skills/<pack-name>/<name>/`` always overwriting,
   because skills are pack-owned canonical content.

Pack discovery uses ``importlib.metadata`` entry-point distributions
that publish into any of our PO groups. Both editable installs
(source-tree layout: ``<pack>/overlay/`` sibling to ``<pack>/po_<mod>/``)
and packaged wheels (``<package>/overlay/`` shipped inside the
importable module) are supported — discovery probes the dist root
first, then the package root.

Used from ``AgentSession.prompt`` (lazily, once per session). Stand-
alone module so it can be unit-tested without spawning Claude.
"""

from __future__ import annotations

import importlib
import logging
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib.metadata import distributions
from pathlib import Path

logger = logging.getLogger(__name__)

PO_ENTRY_POINT_GROUPS = (
    "po.formulas",
    "po.commands",
    "po.doctor_checks",
    "po.deployments",
)


@dataclass(frozen=True)
class Pack:
    """A discovered PO pack on disk."""

    name: str
    """Distribution name as published in ``pyproject.toml`` (e.g. ``po-stripe``)."""

    root: Path
    """Filesystem root containing ``overlay/`` and/or ``skills/``."""

    module_root: Path | None = None
    """The importable package dir (e.g. ``po_stripe/``), used to locate per-role overlays."""


def _resolve_pack_roots(dist) -> tuple[Path, Path | None] | None:
    """Locate the pack source root and its importable module dir.

    Returns ``(dist_root, module_root)`` where ``dist_root`` is the
    directory we probe for top-level ``overlay/`` and ``skills/``, and
    ``module_root`` is the ``po_<mod>/`` dir we use to find per-role
    overlay subtrees. ``module_root`` is ``None`` when we can't
    confidently resolve it.

    For editable installs (the dev workflow), ``dist_root`` is the
    repo dir and ``module_root`` is ``<dist_root>/<package>/``. For
    standard wheel installs both point inside ``site-packages`` and
    ``dist_root == module_root.parent`` (which usually means
    ``site-packages`` itself — see ``Pack.root`` probe order in
    ``apply_overlay`` / ``apply_skills``).
    """
    files = dist.files or []
    py_files = [f for f in files if str(f).endswith("__init__.py")]
    # Find a top-level package: an __init__.py whose parent has no __init__.py.
    module_root: Path | None = None
    for entry in py_files:
        located = dist.locate_file(entry)
        try:
            located_path = Path(located)
        except TypeError:
            continue
        parent = located_path.parent
        if not (parent.parent / "__init__.py").exists():
            module_root = parent
            break

    if module_root is None:
        # Fall back to importing the first entry point's module.
        for group in PO_ENTRY_POINT_GROUPS:
            for ep in dist.entry_points:
                if ep.group != group:
                    continue
                modname = ep.value.split(":", 1)[0]
                try:
                    module = importlib.import_module(modname.split(".", 1)[0])
                except Exception:
                    continue
                file_attr = getattr(module, "__file__", None)
                if file_attr:
                    module_root = Path(file_attr).parent
                    break
            if module_root is not None:
                break

    if module_root is None:
        return None

    dist_root = module_root.parent
    return dist_root, module_root


def discover_packs() -> list[Pack]:
    """Walk installed distributions and return the ones that publish to PO entry-point groups."""
    seen: dict[str, Pack] = {}
    for dist in distributions():
        eps = list(dist.entry_points)
        if not any(ep.group in PO_ENTRY_POINT_GROUPS for ep in eps):
            continue
        name = dist.metadata["Name"]
        if not name or name in seen:
            continue
        roots = _resolve_pack_roots(dist)
        if roots is None:
            logger.debug("pack %s: could not resolve module root, skipping", name)
            continue
        dist_root, module_root = roots
        seen[name] = Pack(name=name, root=dist_root, module_root=module_root)
    return list(seen.values())


def _copy_tree_skip_existing(src: Path, dst: Path) -> list[Path]:
    """Copy ``src/**`` into ``dst/`` preserving mode, skipping any file that already exists at the destination."""
    written: list[Path] = []
    if not src.is_dir():
        return written
    for item in src.rglob("*"):
        if item.is_dir():
            continue
        rel = item.relative_to(src)
        target = dst / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target, follow_symlinks=True)
        written.append(target)
    return written


def _copy_tree_overwrite(src: Path, dst: Path) -> list[Path]:
    """Copy ``src/**`` into ``dst/`` preserving mode, overwriting on conflict."""
    written: list[Path] = []
    if not src.is_dir():
        return written
    for item in src.rglob("*"):
        if item.is_dir():
            continue
        rel = item.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target, follow_symlinks=True)
        written.append(target)
    return written


def _candidate_overlay_dirs(pack: Pack) -> list[Path]:
    """Pack-wide overlay candidates, in order: dist-root first, module-root second."""
    candidates: list[Path] = [pack.root / "overlay"]
    if pack.module_root is not None and pack.module_root != pack.root:
        candidates.append(pack.module_root / "overlay")
    return candidates


def _candidate_skills_dirs(pack: Pack) -> list[Path]:
    candidates: list[Path] = [pack.root / "skills"]
    if pack.module_root is not None and pack.module_root != pack.root:
        candidates.append(pack.module_root / "skills")
    return candidates


def _candidate_role_overlay_dirs(pack: Pack, role: str) -> list[Path]:
    if pack.module_root is None:
        return []
    return [pack.module_root / "agents" / role / "overlay"]


def apply_overlay(pack: Pack, cwd: Path, *, role: str | None = None) -> list[Path]:
    """Materialize the pack's overlay tree into ``cwd``.

    Per-role overlay is laid down before pack-wide so role-specific
    files win via skip-existing semantics. Returns the list of files
    written (for telemetry / tests).
    """
    written: list[Path] = []
    if role:
        for src in _candidate_role_overlay_dirs(pack, role):
            written.extend(_copy_tree_skip_existing(src, cwd))
    for src in _candidate_overlay_dirs(pack):
        written.extend(_copy_tree_skip_existing(src, cwd))
    return written


def apply_skills(pack: Pack, rig_path: Path) -> list[Path]:
    """Copy each ``<pack>/skills/<name>/`` into ``<rig>/.claude/skills/<pack-name>/<name>/``, overwriting."""
    written: list[Path] = []
    dest_root = rig_path / ".claude" / "skills" / pack.name
    for skills_root in _candidate_skills_dirs(pack):
        if not skills_root.is_dir():
            continue
        for skill_dir in sorted(p for p in skills_root.iterdir() if p.is_dir()):
            written.extend(
                _copy_tree_overwrite(skill_dir, dest_root / skill_dir.name)
            )
    return written


def materialize_packs(
    cwd: Path,
    *,
    role: str | None,
    overlay: bool = True,
    skills: bool = True,
    packs: Sequence[Pack] | Iterable[Pack] | None = None,
) -> dict[str, list[Path]]:
    """Apply overlay + skills for every installed pack into ``cwd``.

    Returns a dict mapping ``"<pack-name>:overlay"`` /
    ``"<pack-name>:skills"`` to the files written, mainly for tests.
    """
    results: dict[str, list[Path]] = {}
    if not overlay and not skills:
        return results
    pack_list = list(packs) if packs is not None else discover_packs()
    for pack in pack_list:
        if overlay:
            results[f"{pack.name}:overlay"] = apply_overlay(pack, cwd, role=role)
        if skills:
            results[f"{pack.name}:skills"] = apply_skills(pack, rig_path=cwd)
    return results
