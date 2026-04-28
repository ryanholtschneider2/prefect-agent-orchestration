"""Rig-local pass/fail cache for `software_dev_full`'s per-iter tests.

Lives at ``<rig>/.po-cache/tests.json``. Keyed by
``sha256(layer|source_hash|collection_hash|scope_hash)``. Entries record
the verdict written by the tester role so subsequent iterations on
identical source state can short-circuit the agent turn entirely.

Atomic writes via tempfile + ``os.replace``; read-modify-write is
serialized with ``fcntl.flock`` on a sibling lock file so concurrent
epic flows in the same rig don't drop entries. Untracked junk (e.g.
``__pycache__/``, ``.pyc``) is excluded from ``compute_source_hash`` by
``git ls-files`` semantics, so the key is stable across cosmetic FS
noise.

Public surface:

    cache_key(layer, source_hash, collection_hash, scope_hash) -> str
    compute_source_hash(rig, paths=("prefect_orchestration", "tests")) -> str
    compute_collection_hash(rig, layer, scope) -> str
    compute_scope_hash(scope) -> str
    cache_get(rig, key) -> dict | None
    cache_put(rig, key, verdict, *, run_id, layer, source_hash,
              collection_hash, scope_hash, scope_paths=None) -> None

Stdlib only.
"""

from __future__ import annotations

import datetime as _dt
import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

CACHE_DIRNAME = ".po-cache"
CACHE_FILENAME = "tests.json"
LOCK_FILENAME = "tests.json.lock"
SCHEMA_VERSION = 1

# Sentinels returned when the helper can't compute a real hash. Callers
# treat these as "force a cache miss" so a misconfigured environment
# never short-circuits real test execution.
GIT_FAILED = "GIT_FAILED"
COLLECTION_FAILED = "COLLECTION_FAILED"

_META_FIELDS = frozenset(
    {
        "layer",
        "source_hash",
        "collection_hash",
        "scope_hash",
        "scope_paths",
        "produced_at",
        "produced_by",
    }
)


def _cache_dir(rig: Path) -> Path:
    return Path(rig) / CACHE_DIRNAME


def _cache_path(rig: Path) -> Path:
    return _cache_dir(rig) / CACHE_FILENAME


def _lock_path(rig: Path) -> Path:
    return _cache_dir(rig) / LOCK_FILENAME


def _ensure_dir(rig: Path) -> Path:
    d = _cache_dir(rig)
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_key(
    layer: str, source_hash: str, collection_hash: str, scope_hash: str
) -> str:
    """Deterministic cache key over the four invalidation dimensions."""
    raw = f"{layer}|{source_hash}|{collection_hash}|{scope_hash}".encode()
    return hashlib.sha256(raw).hexdigest()


def compute_source_hash(
    rig: Path, paths: Sequence[str] = ("prefect_orchestration", "tests")
) -> str:
    """sha256 over `git ls-files <paths>` content from the working tree.

    Reads working-tree contents (not HEAD) so uncommitted edits
    invalidate the key. Untracked junk (``__pycache__/``, ``.pyc``,
    ``.tmp`` files, build outputs) is excluded by ``git ls-files``.

    Returns ``GIT_FAILED`` if ``rig`` is not a git repo or git is
    unavailable; callers force a cache miss on this sentinel.
    """
    rig = Path(rig)
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--", *paths],
            cwd=str(rig),
            capture_output=True,
            check=False,
        )
    except OSError:
        return GIT_FAILED
    if out.returncode != 0:
        return GIT_FAILED
    files = sorted(p for p in out.stdout.decode("utf-8", "replace").split("\0") if p)
    h = hashlib.sha256()
    for rel in files:
        full = rig / rel
        h.update(rel.encode())
        h.update(b"\0")
        try:
            h.update(full.read_bytes())
        except OSError:
            h.update(b"<MISSING>")
        h.update(b"\0\0")
    return h.hexdigest()


def compute_collection_hash(rig: Path, layer: str, scope: list[Path] | None) -> str:
    """sha256 of `pytest --collect-only -q --no-header [args]` stdout.

    Captures parametrize / fixture / collection changes that
    `compute_source_hash` would not detect on its own (e.g. a parametrize
    list change in an imported helper). Cheap (~100 ms) compared to
    running the suite.

    Returns ``COLLECTION_FAILED`` on any non-zero exit / timeout / OSError;
    callers force a cache miss on this sentinel and proceed normally.
    """
    rig = Path(rig)
    args: list[str] = [
        "uv",
        "run",
        "python",
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "--no-header",
    ]
    if scope is None:
        layer_dir = {
            "unit": "tests",
            "e2e": "tests/e2e",
            "playwright": "tests/playwright",
        }.get(layer, "tests")
        args.append(layer_dir)
    else:
        args.extend(str(p) for p in scope)
    try:
        out = subprocess.run(
            args,
            cwd=str(rig),
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return COLLECTION_FAILED
    if out.returncode != 0:
        return COLLECTION_FAILED
    return hashlib.sha256(out.stdout).hexdigest()


def compute_scope_hash(scope: Iterable[Path] | None) -> str:
    """sha256 over the sorted scope.

    ``None`` (full layer) hashes to a distinct sentinel so full-suite and
    empty-list cache entries don't collide.
    """
    if scope is None:
        return hashlib.sha256(b"__FULL__").hexdigest()
    body = "\n".join(sorted(str(p) for p in scope))
    return hashlib.sha256(body.encode()).hexdigest()


def _read_raw(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": SCHEMA_VERSION, "entries": {}}
    try:
        text = path.read_text()
    except OSError:
        return {"version": SCHEMA_VERSION, "entries": {}}
    if not text.strip():
        return {"version": SCHEMA_VERSION, "entries": {}}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"version": SCHEMA_VERSION, "entries": {}}
    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        return {"version": SCHEMA_VERSION, "entries": {}}
    if not isinstance(data.get("entries"), dict):
        data["entries"] = {}
    return data


def cache_get(rig: Path, key: str) -> dict[str, Any] | None:
    """Return the cached verdict (without metadata fields) or ``None``."""
    path = _cache_path(rig)
    if not path.is_file():
        return None
    data = _read_raw(path)
    entry = data["entries"].get(key)
    if entry is None:
        return None
    return {k: v for k, v in entry.items() if k not in _META_FIELDS}


def cache_put(
    rig: Path,
    key: str,
    verdict: dict[str, Any],
    *,
    run_id: str = "",
    layer: str = "",
    source_hash: str = "",
    collection_hash: str = "",
    scope_hash: str = "",
    scope_paths: list[str] | None = None,
) -> None:
    """Atomic read-modify-write of the cache file under ``fcntl.flock``.

    Lock target is a sibling ``tests.json.lock`` file (stable inode
    regardless of cache-file replacement). Write is ``tempfile`` →
    ``fsync`` → ``os.replace`` → parent-dir ``fsync`` for crash safety.
    """
    rig = Path(rig)
    cache_dir = _ensure_dir(rig)
    cache_file = _cache_path(rig)
    lock_file = _lock_path(rig)
    lock_file.touch(exist_ok=True)

    with open(lock_file, "r+") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            data = _read_raw(cache_file)
            now = _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")
            entry: dict[str, Any] = {
                **{k: v for k, v in verdict.items() if k not in _META_FIELDS},
                "layer": layer,
                "source_hash": source_hash,
                "collection_hash": collection_hash,
                "scope_hash": scope_hash,
                "scope_paths": list(scope_paths or []),
                "produced_at": now,
                "produced_by": run_id,
            }
            data["entries"][key] = entry
            tmp = tempfile.NamedTemporaryFile(
                mode="w",
                dir=str(cache_dir),
                prefix=".tests.json.",
                suffix=".tmp",
                delete=False,
            )
            tmp_name = tmp.name
            try:
                json.dump(data, tmp, indent=2, sort_keys=True)
                tmp.write("\n")
                tmp.flush()
                os.fsync(tmp.fileno())
            finally:
                tmp.close()
            os.replace(tmp_name, str(cache_file))
            dir_fd = os.open(str(cache_dir), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


__all__ = [
    "CACHE_DIRNAME",
    "CACHE_FILENAME",
    "COLLECTION_FAILED",
    "GIT_FAILED",
    "LOCK_FILENAME",
    "SCHEMA_VERSION",
    "cache_get",
    "cache_key",
    "cache_put",
    "compute_collection_hash",
    "compute_scope_hash",
    "compute_source_hash",
]
