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
import subprocess
import tomllib
from collections.abc import Iterable
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


def _copy_tree(src: Path, dst: Path, *, skip_existing: bool) -> list[Path]:
    """Copy ``src/**`` into ``dst/`` preserving mode.

    With ``skip_existing=True`` files already present at the target are
    left alone; otherwise existing files are overwritten.
    """
    written: list[Path] = []
    if not src.is_dir():
        return written
    for item in src.rglob("*"):
        if item.is_dir():
            continue
        target = dst / item.relative_to(src)
        if skip_existing and target.exists():
            continue
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
            written.extend(_copy_tree(src, cwd, skip_existing=True))
    for src in _candidate_overlay_dirs(pack):
        written.extend(_copy_tree(src, cwd, skip_existing=True))
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
                _copy_tree(skill_dir, dest_root / skill_dir.name, skip_existing=False)
            )
    return written


def _external_skill_refs(pack: Pack) -> list[str]:
    """Read a pack's declared external skill refs from its source pyproject.

    Looks for ``[tool.po] external_skills = ["<ref>", ...]`` in
    ``<pack.root>/pyproject.toml`` (then ``<module_root>/pyproject.toml``).
    Each ref is a ``skills add`` argument: a skills.sh ``author/pkg``, a
    GitHub URL, or ``pkg@skill``. Returns ``[]`` when there is no pyproject
    (e.g. a non-editable wheel install, where the source isn't on disk) or
    no manifest — external skills are opt-in and never required.
    """
    for base in (pack.root, pack.module_root):
        if base is None:
            continue
        pyproject = base / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        refs = ((data.get("tool") or {}).get("po") or {}).get("external_skills")
        if isinstance(refs, list):
            return [r for r in refs if isinstance(r, str) and r.strip()]
        return []
    return []


def apply_external_skills(pack: Pack, rig_path: Path) -> list[str]:
    """Install a pack's declared external skills into the rig (project-level).

    Reads ``[tool.po] external_skills`` (see :func:`_external_skill_refs`) and
    runs ``npx --yes skills add <ref> --project --yes`` (the Vercel ``skills``
    CLI) with ``cwd=rig_path`` for each ref, materializing
    ``<rig>/.claude/skills/`` + a ``skills-lock.json`` (restorable later via
    ``skills experimental_install``). Idempotent — ``skills add`` no-ops when
    the skill is already present.

    Graceful by design: returns ``[]`` when nothing is declared; logs a
    warning and stops (never raises) when ``npx`` is absent, so a pack
    install never fails on a missing optional Node toolchain. A per-ref
    failure is logged and skipped, not fatal.

    Returns the list of refs successfully added.
    """
    refs = _external_skill_refs(pack)
    if not refs:
        return []
    if shutil.which("npx") is None:
        logger.warning(
            "pack %s declares external skills %s but `npx` is not on PATH; "
            "skipping (install Node, or run `npx skills add` in the rig manually)",
            pack.name,
            refs,
        )
        return []
    added: list[str] = []
    for ref in refs:
        proc = subprocess.run(
            ["npx", "--yes", "skills", "add", ref, "--project", "--yes"],
            cwd=str(rig_path),
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            added.append(ref)
        else:
            logger.warning(
                "pack %s: `skills add %s` failed (rc=%s): %s",
                pack.name,
                ref,
                proc.returncode,
                (proc.stderr or proc.stdout or "").strip()[:200],
            )
    return added


def apply_pack_index(pack: Pack, rig_path: Path) -> list[Path]:
    """Copy ``overlay/CLAUDE-*.md`` files to ``<rig>/.claude/packs/``, overwriting."""
    dest = rig_path / ".claude" / "packs"
    written: list[Path] = []
    for src in _candidate_overlay_dirs(pack):
        if not src.is_dir():
            continue
        for f in src.glob("CLAUDE-*.md"):
            target = dest / f.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            written.append(target)
    return written


def materialize_packs(
    cwd: Path,
    *,
    role: str | None,
    overlay: bool = True,
    skills: bool = True,
    index: bool = True,
    packs: Iterable[Pack] | None = None,
) -> dict[str, list[Path]]:
    """Apply overlay + skills + pack index for every installed pack into ``cwd``.

    Returns a dict mapping ``"<pack-name>:overlay"`` /
    ``"<pack-name>:skills"`` / ``"<pack-name>:index"`` to the files
    written, mainly for tests.
    """
    results: dict[str, list[Path]] = {}
    if not overlay and not skills and not index:
        return results
    pack_list = list(packs) if packs is not None else discover_packs()
    for pack in pack_list:
        if overlay:
            results[f"{pack.name}:overlay"] = apply_overlay(pack, cwd, role=role)
        if skills:
            results[f"{pack.name}:skills"] = apply_skills(pack, rig_path=cwd)
        if index:
            results[f"{pack.name}:index"] = apply_pack_index(pack, rig_path=cwd)
    return results
