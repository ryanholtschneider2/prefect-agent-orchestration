"""Load an ad-hoc Prefect `@flow` from a `.py` file on disk.

Backs `po run --from-file <path> [--name <flow>]`. Imports the file under
a stable synthetic module name (`po_scratch_<sha1[:10]>`), walks its
attributes for objects that are Prefect `Flow`s, and returns the unique
match (or the one selected by `name`).
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any


class ScratchLoadError(Exception):
    """Raised when a scratch file can't be resolved to a single Flow."""


def _synthetic_module_name(abspath: Path) -> str:
    digest = hashlib.sha1(str(abspath).encode("utf-8")).hexdigest()[:10]
    return f"po_scratch_{digest}"


def _is_prefect_flow(obj: Any) -> bool:
    try:
        from prefect.flows import Flow
    except Exception:  # pragma: no cover — Prefect is a hard dep of PO
        return False
    return isinstance(obj, Flow)


def load_flow_from_file(path: Path, name: str | None = None) -> Any:
    """Import `path` and return a Prefect `@flow` object.

    Resolves `path` against CWD. If the file defines exactly one flow,
    `name` may be omitted; otherwise pass `name` to disambiguate.
    """
    abspath = Path(path).expanduser().resolve()
    if not abspath.exists():
        raise ScratchLoadError(f"no such file: {abspath}")
    if abspath.is_dir():
        raise ScratchLoadError(f"--from-file expects a .py file, got dir: {abspath}")
    if abspath.suffix != ".py":
        raise ScratchLoadError(
            f"--from-file expects a .py file, got {abspath.suffix or '<no suffix>'}: {abspath}"
        )

    mod_name = _synthetic_module_name(abspath)
    if mod_name in sys.modules:
        module = sys.modules[mod_name]
    else:
        spec = importlib.util.spec_from_file_location(mod_name, abspath)
        if spec is None or spec.loader is None:
            raise ScratchLoadError(f"could not build import spec for {abspath}")
        module = importlib.util.module_from_spec(spec)
        # Insert before exec_module so internal self-refs (e.g. dataclasses,
        # decorators that look up the module) resolve.
        sys.modules[mod_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(mod_name, None)
            raise

    candidates: dict[str, Any] = {}
    for attr in dir(module):
        if attr.startswith("_"):
            continue
        obj = getattr(module, attr, None)
        if _is_prefect_flow(obj):
            candidates[attr] = obj

    if not candidates:
        raise ScratchLoadError(
            f"no Prefect @flow-decorated callables found in {abspath}. "
            "Decorate at least one function with `@flow`."
        )

    if name is not None:
        if name in candidates:
            return candidates[name]
        # Allow matching by the flow's own .name attribute too.
        for obj in candidates.values():
            if getattr(obj, "name", None) == name:
                return obj
        listing = ", ".join(sorted(candidates))
        raise ScratchLoadError(
            f"no flow named {name!r} in {abspath}. candidates: {listing}"
        )

    if len(candidates) == 1:
        return next(iter(candidates.values()))

    listing = ", ".join(sorted(candidates))
    raise ScratchLoadError(
        f"{abspath} defines {len(candidates)} flows ({listing}); "
        "pass --name <flow> to pick one."
    )
