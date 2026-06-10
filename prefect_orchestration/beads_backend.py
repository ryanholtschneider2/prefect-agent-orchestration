"""Backend-agnostic verdict + dep-graph seam over the beads CLI.

PO's verdict channel was historically dolt-specific: agents stamped
``bd update <id> --metadata '{"po.<role>": ...}'`` and the orchestrator read
``metadata["po.<role>"]``. ``beads_rust`` (``br``) has no per-issue arbitrary
metadata, so this module re-homes the channel behind a seam:

  - **dolt** — verdict lives in the iter bead's ``metadata["po.<name>"]``.
  - **br** — verdict lives in an append-only comment
    ``po-verdict:<name>:<json>``; the latest (max comment ``id``) wins.
    Append-only sidesteps the read-modify-write race a single shared blob
    would hit across concurrent roles, and the monotonic integer ``id`` makes
    "latest" unambiguous without timestamp parsing.

Selection (:func:`resolve_backend`): ``PO_BEADS_BACKEND`` env override wins;
else sniff ``.beads/metadata.json`` (``dolt_mode`` present -> dolt;
``database`` + ``jsonl_export`` and no ``dolt_mode`` -> br); else default
``"dolt"``. Dolt rigs are untouched — the existing path is preserved exactly.

The dep-graph rows br emits are shaped differently from bd's (no ``id`` key;
``issue_id``/``depends_on_id`` name the two endpoints), so
:func:`normalize_dep_rows` re-keys them per-direction for the ``row["id"]``
consumers in ``beads_meta`` (``resolve_seed_bead`` / ``list_subgraph``).
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

# Maps backend name -> the CLI binary that drives it.
BINARY: dict[str, str] = {"dolt": "bd", "br": "br"}

_VERDICT_PREFIX = "po-verdict:"


@functools.lru_cache(maxsize=1)
def _bd_is_really_br() -> bool:
    """True when the ``bd`` on PATH is actually beads-rust (``bd`` -> ``br``).

    After the machine-wide ``bd``->``br`` migration the ``bd`` binary has no
    ``--id`` / ``--set-metadata`` (it is a symlink to ``br``). A rig whose
    ``.beads/metadata.json`` still names a dolt backend would otherwise take a
    dolt code path (``bd create --id=…``) that the ``br`` binary rejects with
    ``unexpected argument '--id'`` — silently breaking agentic dispatch
    (prefect-orchestration-3d7y). We probe the binary (``bd --version`` prints
    ``br <x.y.z>`` for beads-rust) so the *binary* is the ground truth, not a
    stale metadata file. Cached for the process — the binary doesn't change
    under us. Returns ``False`` when ``bd`` is absent or the probe fails (the
    safe, behaviour-preserving answer for a genuine dolt rig).
    """
    bd = shutil.which("bd")
    if bd is None:
        return False
    try:
        proc = subprocess.run(
            [bd, "--version"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.stdout.strip().lower().startswith("br")


def resolve_backend(rig_path: Path | str | None) -> str:
    """Return the beads backend (``"dolt"`` or ``"br"``) for *rig_path*.

    Precedence: ``PO_BEADS_BACKEND`` env override > sniff
    ``<rig_path>/.beads/metadata.json`` (``dolt_mode`` present -> dolt;
    ``database`` + ``jsonl_export`` and no ``dolt_mode`` -> br) > default
    ``"dolt"`` — **except** that a sniffed/defaulted ``"dolt"`` is downgraded to
    ``"br"`` when the on-PATH ``bd`` binary is actually beads-rust (see
    :func:`_bd_is_really_br`). The binary is ground truth: a dolt code path
    against a ``br`` binary fails, so a stale ``metadata.json`` left behind by
    the migration must not win. Only an explicit ``PO_BEADS_BACKEND`` override
    forces dolt against the binary's word.
    """
    env = os.environ.get("PO_BEADS_BACKEND")
    if env:
        env = env.strip().lower()
        if env in BINARY:
            return env
    sniffed = _sniff_backend(rig_path)
    if sniffed == "dolt" and _bd_is_really_br():
        return "br"
    return sniffed


def _sniff_backend(rig_path: Path | str | None) -> str:
    """The ``.beads/metadata.json`` sniff alone (no binary-capability guard).

    ``dolt_mode`` present -> dolt; ``database`` + ``jsonl_export`` and no
    ``dolt_mode`` -> br; absent/unreadable/ambiguous -> ``"dolt"``.
    """
    base = Path(rig_path) if rig_path is not None else Path.cwd()
    try:
        meta = json.loads((base / ".beads" / "metadata.json").read_text())
    except (OSError, json.JSONDecodeError):
        return "dolt"
    if not isinstance(meta, dict):
        return "dolt"
    if "dolt_mode" in meta:
        return "dolt"
    if "database" in meta and "jsonl_export" in meta:
        return "br"
    return "dolt"


def read_verdict(
    bead_id: str,
    name: str,
    *,
    backend: str,
    rig_path: Path | str | None,
    timeout: int,
) -> dict[str, Any]:
    """Read the ``<name>`` verdict off *bead_id* using *backend*.

    Both backends honour the same exception contract so the retry/cache
    wrapper in ``parsing.read_bead_verdict`` is unaffected:

      - ``FileNotFoundError`` — the bead read failed (non-zero exit / empty
        stdout). Transient; the wrapper retries.
      - ``ValueError`` — the CLI output was not parseable JSON. Transient.
      - ``KeyError`` — the bead exists but carries no matching verdict. A
        semantic failure; the wrapper never retries it.
    """
    if backend == "br":
        return _read_verdict_br(bead_id, name, rig_path=rig_path, timeout=timeout)
    return _read_verdict_dolt(bead_id, name, rig_path=rig_path, timeout=timeout)


def _read_verdict_dolt(
    bead_id: str,
    name: str,
    *,
    rig_path: Path | str | None,
    timeout: int,
) -> dict[str, Any]:
    """dolt read path — verbatim the historical ``_bd_show_once`` body."""
    proc = subprocess.run(
        ["bd", "show", bead_id, "--json"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(rig_path) if rig_path is not None else None,
        timeout=timeout,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise FileNotFoundError(
            f"bd show {bead_id} returned no rows "
            f"(rc={proc.returncode}, stderr={proc.stderr[:200]!r})"
        )
    try:
        rows = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"bd show {bead_id} --json was not parseable:\n{proc.stdout[:500]}"
        ) from exc
    issue = rows[0] if isinstance(rows, list) else rows
    if not isinstance(issue, dict):
        raise FileNotFoundError(f"bd show {bead_id} returned no issue rows")
    metadata = issue.get("metadata") or {}
    key = f"po.{name}"
    if key not in metadata:
        raise KeyError(
            f"bead {bead_id} has no metadata key {key!r}. "
            f"Agent likely skipped the `bd update ... --metadata '{{\"{key}\": ...}}'` step. "
            f"Available keys: {sorted(metadata.keys())}"
        )
    value = metadata[key]
    # `--set-metadata k=v` stores values as strings, even if v looks like JSON.
    # `--metadata '<json>'` stores native objects. Accept both shapes — if the
    # stored value is a string that parses as JSON, decode it for the caller.
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {"value": value}
    if isinstance(value, dict):
        return value
    return {"value": value}


def _read_verdict_br(
    bead_id: str,
    name: str,
    *,
    rig_path: Path | str | None,
    timeout: int,
) -> dict[str, Any]:
    """br read path — latest ``po-verdict:<name>:<json>`` comment wins."""
    proc = subprocess.run(
        ["br", "show", bead_id, "--json"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(rig_path) if rig_path is not None else None,
        timeout=timeout,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise FileNotFoundError(
            f"br show {bead_id} returned no rows "
            f"(rc={proc.returncode}, stderr={proc.stderr[:200]!r})"
        )
    try:
        rows = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"br show {bead_id} --json was not parseable:\n{proc.stdout[:500]}"
        ) from exc
    issue = rows[0] if isinstance(rows, list) else rows
    if not isinstance(issue, dict):
        raise FileNotFoundError(f"br show {bead_id} returned no issue rows")
    prefix = f"{_VERDICT_PREFIX}{name}:"
    matching = [
        c
        for c in (issue.get("comments") or [])
        if isinstance(c, dict)
        and isinstance(c.get("text"), str)
        and c["text"].startswith(prefix)
    ]
    if not matching:
        raise KeyError(
            f"bead {bead_id} has no {prefix!r} comment. "
            f"Agent likely skipped the `br comments add {bead_id} "
            f"'{prefix}<json>'` step."
        )
    latest = max(matching, key=lambda c: c.get("id", 0))
    payload = latest["text"][len(prefix) :]
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError:
        return {"value": payload}
    return decoded if isinstance(decoded, dict) else {"value": decoded}


def write_verdict(
    bead_id: str,
    name: str,
    payload: Any,
    *,
    backend: str,
    rig_path: Path | str | None,
) -> None:
    """Write the ``<name>`` verdict onto *bead_id* using *backend*.

    dolt stamps ``metadata["po.<name>"]``; br appends a
    ``po-verdict:<name>:<json>`` comment. Raises ``RuntimeError`` on a
    non-zero CLI exit.
    """
    blob = json.dumps(payload)
    if backend == "br":
        cmd = ["br", "comments", "add", bead_id, f"{_VERDICT_PREFIX}{name}:{blob}"]
    else:
        cmd = ["bd", "update", bead_id, "--set-metadata", f"po.{name}={blob}"]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(rig_path) if rig_path is not None else None,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{cmd[0]} write_verdict for {bead_id}.{name} failed "
            f"(rc={proc.returncode}): {proc.stderr[:200]}"
        )


def normalize_dep_rows(
    rows: list[dict],
    *,
    direction: str,
    backend: str,
) -> list[dict]:
    """Re-key br dep rows to the ``{"id", "status", "title"}`` shape.

    dolt rows already carry ``id`` — passthrough. br rows name the two
    endpoints as ``issue_id`` (the dependent) and ``depends_on_id`` (the
    prereq); the row's ``status``/``title`` describe the *other* endpoint.
    For ``--direction=up`` that endpoint is ``issue_id``; for ``--direction=down``
    it is ``depends_on_id``. Adding ``id`` keeps ``resolve_seed_bead`` /
    ``list_subgraph`` (which read ``row["id"]``) working unchanged.
    """
    if backend != "br":
        return rows
    id_key = "issue_id" if direction == "up" else "depends_on_id"
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized = dict(row)
        normalized["id"] = row.get(id_key)
        out.append(normalized)
    return out
