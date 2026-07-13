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
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from importlib.metadata import distributions
from pathlib import Path

PACK_ENTRY_POINT_GROUPS: tuple[str, ...] = (
    "po.formulas",
    "po.deployments",
    "po.commands",
    "po.doctor_checks",
    "po.env_drivers",
)

CORE_DISTRIBUTION = "prefect-orchestration"
PACKS_MANIFEST_ENV = "PO_PACKS_MANIFEST"

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


@dataclass(frozen=True)
class PackRequirement:
    """A restorable package requirement kept outside uv's tool receipt."""

    name: str
    spec: str
    editable: bool


def packs_manifest_path() -> Path:
    override = os.environ.get(PACKS_MANIFEST_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "po" / "packs.json"


def _requirement_for_pack(pack: PackInfo) -> PackRequirement:
    editable = pack.source == "editable" and bool(pack.source_detail)
    return PackRequirement(
        name=pack.name,
        spec=pack.source_detail if editable else pack.name,
        editable=editable,
    )


def _load_manifest() -> tuple[PackRequirement, list[PackRequirement]]:
    """Return the desired core and pack set; malformed state fails loudly."""
    path = packs_manifest_path()
    if not path.exists():
        return PackRequirement(CORE_DISTRIBUTION, CORE_DISTRIBUTION, False), []
    try:
        payload = json.loads(path.read_text())
        core_raw = payload.get("core") or {}
        core = PackRequirement(
            CORE_DISTRIBUTION,
            str(core_raw.get("spec") or CORE_DISTRIBUTION),
            bool(core_raw.get("editable", False)),
        )
        requirements = [
            PackRequirement(
                name=str(item["name"]),
                spec=str(item["spec"]),
                editable=bool(item.get("editable", False)),
            )
            for item in payload.get("packs", [])
        ]
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise PackError(f"invalid PO pack manifest {path}: {exc}") from exc
    return core, requirements


def _write_manifest(
    core: PackRequirement, requirements: list[PackRequirement]
) -> None:
    path = packs_manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "core": {"spec": core.spec, "editable": core.editable},
        "packs": [
            {"name": req.name, "spec": req.spec, "editable": req.editable}
            for req in sorted(requirements, key=lambda item: _norm_dist(item.name))
        ],
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def _merge_discovered(
    requirements: list[PackRequirement], discovered: list[PackInfo]
) -> list[PackRequirement]:
    """Bootstrap/update durable intent from packs visible before a rebuild."""
    merged = {_norm_dist(req.name): req for req in requirements}
    for pack in discovered:
        if _norm_dist(pack.name) != _norm_dist(CORE_DISTRIBUTION):
            merged[_norm_dist(pack.name)] = _requirement_for_pack(pack)
    return list(merged.values())


def _requirements_argv(requirements: list[PackRequirement]) -> list[str]:
    argv: list[str] = []
    for req in requirements:
        if req.editable:
            # The target pack may carry a stale/different local source for this
            # dependency. Our durable manifest is authoritative and the
            # explicit editable requirement below supplies the chosen path.
            argv += ["--no-sources-package", req.name, "--with-editable", req.spec]
        else:
            argv += ["--with", req.spec]
    return argv


def _core_install_argv(core: PackRequirement) -> list[str]:
    argv = ["tool", "install", "--reinstall"]
    if core.editable:
        # A sibling editable pack may pin core through its own tool.uv.sources
        # table. The explicitly selected core checkout is authoritative here;
        # otherwise uv rejects the two local URLs as conflicting.
        return [
            *argv,
            "--no-sources-package",
            CORE_DISTRIBUTION,
            "--editable",
            core.spec,
        ]
    return [*argv, core.spec]


def _is_core_path(spec: str) -> bool:
    """Identify an editable checkout of this distribution without importing it."""
    name = _distribution_name_for_spec(spec)
    return bool(name and _norm_dist(name) == _norm_dist(CORE_DISTRIBUTION))


def _distribution_name_for_spec(spec: str) -> str | None:
    """Read local project identity so a new editable path replaces the old one."""
    path = Path(spec)
    if not path.is_dir():
        return spec if classify_spec(spec) == "pypi" else None
    try:
        payload = tomllib.loads((path / "pyproject.toml").read_text())
        name = payload.get("project", {}).get("name")
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        return None
    return str(name) if name else None


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


def _pack_with_args(pack: PackInfo) -> list[str]:
    """Argv fragment that re-adds an already-installed pack to the tool env.

    Editable packs are re-listed by their on-disk path (`--with-editable`)
    so the live source stays wired; everything else rides along by
    distribution name (`--with`).
    """
    if pack.source == "editable" and pack.source_detail:
        return ["--with-editable", pack.source_detail]
    return ["--with", pack.name]


def _norm_dist(name: str) -> str:
    """Normalize a distribution name for comparison (PEP 503-ish)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _same_pack(pack: PackInfo, spec: str, *, editable: bool) -> bool:
    """Best-effort: does `pack` already represent the pack named by `spec`?

    Used to drop the pack being (re)installed from the preserved-existing
    set so it isn't listed twice in the `uv tool install` argv. Matches on
    resolved filesystem path for editable/local specs, else on normalized
    distribution name.
    """
    if pack.source_detail and (editable or pack.source in ("editable", "local")):
        try:
            spec_path = Path(spec)
            if (
                spec_path.exists()
                and Path(pack.source_detail).resolve() == spec_path.resolve()
            ):
                return True
        except OSError:
            pass
    return _norm_dist(pack.name) == _norm_dist(spec)


def _install_argv(
    spec: str, *, editable: bool, existing: list[PackInfo] | None = None
) -> list[str]:
    """Build `uv tool install …` argv for adding a pack alongside core.

    `--reinstall` on the core distribution rebuilds the tool env from
    EXACTLY the `--with` / `--with-editable` set passed here — so to keep
    the install additive we must re-list every pack already in the env
    (`existing`) alongside the new one. Omitting them is the footgun that
    evicted the software-dev packs when a Director pack was installed
    (prefect-orchestration-7zi). Passing no `existing` reproduces the bare
    single-pack argv (used by the direct argv unit tests).
    """
    argv = ["tool", "install", "--reinstall", CORE_DISTRIBUTION]
    for pack in existing or []:
        if pack.name == CORE_DISTRIBUTION:
            continue
        argv += _pack_with_args(pack)
    argv += ["--with-editable" if editable else "--with", spec]
    return argv


def install(spec: str, *, editable: bool = False) -> None:
    """Install a pack additively, preserving every pack already installed.

    Disambiguates `spec` when `editable` is False. Enumerates the packs
    currently in the tool env and re-lists them in the same
    `uv tool install --reinstall` call so installing a new pack never
    evicts the others (prefect-orchestration-7zi).

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
    discovered = discover_packs()
    manifest_core, manifest_packs = _load_manifest()
    manifest_packs = _merge_discovered(manifest_packs, discovered)
    target_name = _distribution_name_for_spec(spec)
    if _is_core_path(spec):
        core = PackRequirement(CORE_DISTRIBUTION, spec, eff_editable)
        argv = _core_install_argv(core)
        argv += _requirements_argv(manifest_packs)
        # Persist intent before uv mutates the environment. If uv is interrupted,
        # `po packs restore` still knows what the complete environment should be.
        _write_manifest(core, manifest_packs)
    else:
        core = manifest_core
        existing = [
            p
            for p in discovered
            if p.name != CORE_DISTRIBUTION
            and not (
                target_name and _norm_dist(p.name) == _norm_dist(target_name)
            )
            and not _same_pack(p, spec, editable=eff_editable)
        ]
        # Prefer durable requirements because a prior raw uv reinstall may have
        # already evicted packages from discoverable entry-point metadata.
        preserved = {
            _norm_dist(req.name): req
            for req in manifest_packs
            if not (
                target_name and _norm_dist(req.name) == _norm_dist(target_name)
            )
            and not _same_pack(
                PackInfo(req.name, "", "editable" if req.editable else "pypi", req.spec),
                spec,
                editable=eff_editable,
            )
        }
        for pack in existing:
            preserved[_norm_dist(pack.name)] = _requirement_for_pack(pack)
        argv = _core_install_argv(core)
        argv += _requirements_argv(list(preserved.values()))
        argv += ["--with-editable" if eff_editable else "--with", spec]
    try:
        _run_uv(argv)
    except subprocess.CalledProcessError as exc:
        raise PackError(
            f"po install {spec!r} failed (uv exited {exc.returncode}):\n"
            f"{(exc.stderr or '').rstrip()}"
        ) from exc
    installed = discover_packs()
    final_packs = _merge_discovered(manifest_packs, installed)
    if not _is_core_path(spec):
        matched = next(
            (pack for pack in installed if _same_pack(pack, spec, editable=eff_editable)),
            None,
        )
        if matched is not None:
            final_packs = _merge_discovered(final_packs, [matched])
        elif not any(req.spec == spec for req in final_packs):
            final_packs = [
                req
                for req in final_packs
                if not (
                    target_name and _norm_dist(req.name) == _norm_dist(target_name)
                )
            ]
            final_packs.append(
                PackRequirement(target_name or spec, spec, eff_editable)
            )
    _write_manifest(core, final_packs)
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
    lines = ["po command name(s) collide with core verbs — refusing to register:"]
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
        current = discover_packs()
        remaining = [
            p
            for p in current
            if p.name != name and p.name != CORE_DISTRIBUTION
        ]
        core, manifest_packs = _load_manifest()
        removed_norms = {_norm_dist(name)}
        removed_norms.update(
            _norm_dist(pack.name) for pack in current if _norm_dist(pack.name) == _norm_dist(name)
        )
        desired = [
            req for req in _merge_discovered(manifest_packs, remaining)
            if _norm_dist(req.name) not in removed_norms
        ]
        argv = _core_install_argv(core)
        argv += _requirements_argv(desired)
        _write_manifest(core, desired)
        _run_uv(argv)
    except subprocess.CalledProcessError as exc:
        raise PackError(
            f"po uninstall {name!r} failed (uv exited {exc.returncode}):\n"
            f"{(exc.stderr or '').rstrip()}"
        ) from exc


def update(name: str | None = None) -> list[str]:
    """Reinstall one pack (or all) so EP metadata refreshes.

    The `uv tool install --reinstall` always lists the FULL set of
    installed packs so refreshing one never evicts the others — the same
    additive-install footgun applies here (prefect-orchestration-7zi).
    `name` only narrows the returned set (which packs the caller asked to
    refresh); the env always stays whole.

    Returns the list of pack names that were refreshed.
    """
    packs = discover_packs()
    all_packs = [p for p in packs if p.name != CORE_DISTRIBUTION]
    if name is None:
        targets = all_packs
    else:
        targets = [p for p in all_packs if p.name == name]
        if not targets:
            raise PackError(
                f"no installed pack named {name!r}. Run `po packs` to see what's installed."
            )

    if not all_packs:
        # Still reinstall core so its own EP metadata refreshes.
        _run_uv(["tool", "install", "--reinstall", CORE_DISTRIBUTION])
        return []

    # One aggregated reinstall keeps the tool env consistent and rewrites
    # EP metadata for every extra in the same pass. List ALL packs, not
    # just the targets, so a single-pack refresh doesn't drop the rest.
    argv: list[str] = ["tool", "install", "--reinstall", CORE_DISTRIBUTION]
    for pack in all_packs:
        argv += _pack_with_args(pack)
    try:
        _run_uv(argv)
    except subprocess.CalledProcessError as exc:
        raise PackError(
            f"po update failed (uv exited {exc.returncode}):\n"
            f"{(exc.stderr or '').rstrip()}"
        ) from exc
    return [p.name for p in targets]


def restore() -> list[str]:
    """Rebuild the complete PO tool environment from durable desired state."""
    core, requirements = _load_manifest()
    argv = _core_install_argv(core)
    argv += _requirements_argv(requirements)
    try:
        _run_uv(argv)
    except subprocess.CalledProcessError as exc:
        raise PackError(
            f"po packs restore failed (uv exited {exc.returncode}):\n"
            f"{(exc.stderr or '').rstrip()}"
        ) from exc
    return [req.name for req in requirements]


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
