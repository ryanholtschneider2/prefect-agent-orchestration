"""Pack lifecycle — `po install / update / uninstall / packs`.

PO owns pack lifecycle end-to-end (engdocs/principles.md §3). Users
should only know `po` and `prefect`; they should not have to learn
`uv tool install --force --editable …` incantations.

Under the hood every verb shells out through a single `_run_uv` seam
(trivially monkeypatched in tests). `po` itself lives in a uv-managed
tool env (installed via `uv tool install prefect-orchestration`); packs
ride along as `--with` / `--with-editable` extras so entry-points from
every installed pack are visible to the same Python process.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from importlib.metadata import distributions
from pathlib import Path

PACK_ENTRY_POINT_GROUPS: tuple[str, ...] = (
    "po.formulas",
    "po.deployments",
    "po.commands",
    "po.doctor_checks",
)

CORE_DISTRIBUTION = "prefect-orchestration"

# `po` lives in the same uv tool env as its packs. These canonical argv
# shapes are covered by unit tests so flag-drift in uv is caught fast.
_UV_INSTALL_POINTER = (
    "po requires the `uv` package manager.\n"
    "  Install: curl -LsSf https://astral.sh/uv/install.sh | sh\n"
    "  Then re-run: po <command>"
)


class PackError(RuntimeError):
    """Raised for pack-lifecycle failures surfaced to the CLI."""


@dataclass(frozen=True)
class PackInfo:
    name: str
    version: str
    source: str  # "pypi" | "editable" | "git" | "local" | "unknown"
    source_detail: str = ""
    contributions: dict[str, list[str]] = field(default_factory=dict)


def find_uv() -> str:
    """Return the absolute path to `uv`, or raise `PackError`."""
    path = shutil.which("uv")
    if not path:
        raise PackError(_UV_INSTALL_POINTER)
    return path


def _run_uv(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Single subprocess seam — monkeypatched in tests."""
    uv = find_uv()
    return subprocess.run(
        [uv, *args],
        check=True,
        capture_output=True,
        text=True,
    )


_GIT_URL_RE = re.compile(
    r"""^(
        git\+.+          # git+https://, git+ssh://, git+file://, ...
      | git@[^:]+:.+     # git@host:org/repo
      | https?://.+\.git(@.*)?$  # https URL ending with .git (maybe ref)
    )""",
    re.VERBOSE,
)


def classify_spec(spec: str) -> str:
    """Return one of: 'git', 'path', 'pypi' — without touching the filesystem for non-paths."""
    if _GIT_URL_RE.match(spec):
        return "git"
    p = Path(spec)
    # Only treat as path if it actually exists locally — PyPI names
    # could collide with nonexistent paths.
    if p.exists() and p.is_dir():
        return "path"
    return "pypi"


def _install_argv(spec: str, *, editable: bool) -> list[str]:
    """Build `uv tool install …` argv for adding a pack alongside core.

    Uses `--upgrade` on the core distribution with a `--with` (or
    `--with-editable`) extra so the pack lands in the same tool env.
    `--reinstall` forces uv to rewrite entry-point metadata for both
    core and the extra pack (the "refresh EP metadata" footgun the
    manual command had).
    """
    return [
        "tool",
        "install",
        "--reinstall",
        CORE_DISTRIBUTION,
        "--with-editable" if editable else "--with",
        spec,
    ]


def install(spec: str, *, editable: bool = False) -> None:
    """Install a pack. Disambiguates `spec` when `editable` is False.

    After uv finishes, audits any newly-discovered `po.commands` entries
    against the core-verb set and raises `PackError` on collision so
    pack authors hear about it at install time, not invocation time.
    """
    if not spec:
        raise PackError("install: spec must not be empty")
    eff_editable = editable
    if not editable:
        kind = classify_spec(spec)
        if kind == "path":
            eff_editable = True
    try:
        _run_uv(_install_argv(spec, editable=eff_editable))
    except subprocess.CalledProcessError as exc:
        raise PackError(
            f"po install {spec!r} failed (uv exited {exc.returncode}):\n"
            f"{(exc.stderr or '').rstrip()}"
        ) from exc
    _check_command_collisions()


def _check_command_collisions() -> None:
    """Scan installed packs for `po.commands` entries that shadow core verbs.

    Loud at install/update time per principle §4: collisions break
    `po <command>` dispatch (core verb wins) and would otherwise only
    surface as a silent no-op much later. We do not auto-uninstall —
    leaves the user in control. Lazily imports `commands` to avoid a
    circular import at module load.
    """
    from prefect_orchestration import commands as _cmds

    by_pack: dict[str, list[str]] = {}
    for p in discover_packs():
        names = p.contributions.get("po.commands") or []
        if names:
            by_pack[p.name] = list(names)
    collisions = _cmds.find_command_collisions(by_pack)
    if not collisions:
        return
    lines = [
        "po command name(s) collide with core verbs — refusing to register:"
    ]
    for pack, offenders in sorted(collisions.items()):
        lines.append(f"  {pack}: {', '.join(offenders)}")
    lines.append(
        "remove the offending entry from the pack's pyproject.toml, "
        "or run `po uninstall <pack>` to roll back."
    )
    raise PackError("\n".join(lines))


def uninstall(name: str) -> None:
    """Remove a pack from po's tool env.

    Guards against `po uninstall prefect-orchestration` — that would
    remove po itself. Surfaces the manual uv escape hatch so users who
    really mean it aren't blocked.
    """
    if not name:
        raise PackError("uninstall: name must not be empty")
    if name == CORE_DISTRIBUTION:
        raise PackError(
            f"refusing to uninstall {CORE_DISTRIBUTION} — that would remove `po` "
            f"itself.\nIf you really mean it, run: uv tool uninstall {CORE_DISTRIBUTION}"
        )
    try:
        # `uv tool install --reinstall <core>` with no --with drops the
        # named pack from the env. But uv has no per-extra removal, so
        # we re-install core with every OTHER pack except this one.
        remaining = [
            p
            for p in discover_packs()
            if p.name != name and p.name != CORE_DISTRIBUTION
        ]
        argv: list[str] = ["tool", "install", "--reinstall", CORE_DISTRIBUTION]
        for pack in remaining:
            if pack.source == "editable" and pack.source_detail:
                argv += ["--with-editable", pack.source_detail]
            else:
                argv += ["--with", pack.name]
        _run_uv(argv)
    except subprocess.CalledProcessError as exc:
        raise PackError(
            f"po uninstall {name!r} failed (uv exited {exc.returncode}):\n"
            f"{(exc.stderr or '').rstrip()}"
        ) from exc


def update(name: str | None = None) -> list[str]:
    """Reinstall one pack (or all) so EP metadata refreshes.

    Returns the list of pack names that were refreshed.
    """
    packs = discover_packs()
    if name is None:
        targets = [p for p in packs if p.name != CORE_DISTRIBUTION]
    else:
        targets = [p for p in packs if p.name == name]
        if not targets:
            raise PackError(
                f"no installed pack named {name!r}. Run `po packs` to see what's installed."
            )

    if not targets:
        # Still reinstall core so its own EP metadata refreshes.
        _run_uv(["tool", "install", "--reinstall", CORE_DISTRIBUTION])
        return []

    # One aggregated reinstall keeps the tool env consistent and rewrites
    # EP metadata for every extra in the same pass.
    argv: list[str] = ["tool", "install", "--reinstall", CORE_DISTRIBUTION]
    for pack in targets:
        if pack.source == "editable" and pack.source_detail:
            argv += ["--with-editable", pack.source_detail]
        else:
            argv += ["--with", pack.name]
    try:
        _run_uv(argv)
    except subprocess.CalledProcessError as exc:
        raise PackError(
            f"po update failed (uv exited {exc.returncode}):\n"
            f"{(exc.stderr or '').rstrip()}"
        ) from exc
    return [p.name for p in targets]


def _source_for_dist(dist: object) -> tuple[str, str]:
    """Classify a distribution's install source via PEP 610 `direct_url.json`.

    Returns `(source, detail)` where source ∈ {"pypi", "editable", "git",
    "local", "unknown"} and detail is a URL or filesystem path when known.
    """
    try:
        raw = dist.read_text("direct_url.json")  # type: ignore[attr-defined]
    except Exception:
        raw = None
    if not raw:
        return "pypi", ""
    try:
        data = json.loads(raw)
    except ValueError:
        return "unknown", ""
    url = data.get("url", "")
    if "vcs_info" in data:
        return "git", url
    dir_info = data.get("dir_info") or {}
    if dir_info.get("editable"):
        detail = url
        if detail.startswith("file://"):
            detail = detail[len("file://") :]
        return "editable", detail
    # Non-editable local path (rare)
    if url.startswith("file://"):
        return "local", url[len("file://") :]
    return "unknown", url


def _contributions_for_dist(dist: object) -> dict[str, list[str]]:
    eps = getattr(dist, "entry_points", [])
    out: dict[str, list[str]] = {}
    for ep in eps:
        group = getattr(ep, "group", None)
        if group in PACK_ENTRY_POINT_GROUPS:
            out.setdefault(group, []).append(ep.name)
    for names in out.values():
        names.sort()
    return out


def discover_packs() -> list[PackInfo]:
    """Scan every installed distribution and return those that contribute po.* EPs.

    Always includes `prefect-orchestration` itself if it's installed, so
    `po packs` can show the core version too.
    """
    result: list[PackInfo] = []
    for dist in distributions():
        meta = dist.metadata
        name = meta["Name"] if meta else getattr(dist, "name", None)
        if not name:
            continue
        contributions = _contributions_for_dist(dist)
        if not contributions and name != CORE_DISTRIBUTION:
            continue
        version = meta["Version"] if meta else ""
        source, detail = _source_for_dist(dist)
        result.append(
            PackInfo(
                name=name,
                version=version or "",
                source=source,
                source_detail=detail,
                contributions=contributions,
            )
        )
    result.sort(key=lambda p: p.name)
    return result


def render_packs_table(packs: list[PackInfo]) -> str:
    """Plain-text table for `po packs`. No extra deps."""
    if not packs:
        return "no packs installed."
    headers = ("NAME", "VERSION", "SOURCE", "CONTRIBUTES")
    rows: list[tuple[str, str, str, str]] = []
    for p in packs:
        src = p.source
        if p.source_detail and p.source in ("editable", "git", "local"):
            src = f"{p.source}:{p.source_detail}"
        if p.contributions:
            parts = []
            for group in PACK_ENTRY_POINT_GROUPS:
                names = p.contributions.get(group)
                if not names:
                    continue
                short = group.removeprefix("po.")
                parts.append(f"{short}={','.join(names)}")
            contributes = "  ".join(parts)
        else:
            contributes = "-"
        rows.append((p.name, p.version, src, contributes))
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
    for row in rows:
        lines.append(fmt.format(*row))
    return "\n".join(lines)
