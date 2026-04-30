"""Resolve an issue id → on-disk run dir via bead metadata.

The formula flow writes `po.rig_path` and `po.run_dir` to
the parent bead at entry. Verbs like `po logs`, `po artifacts`,
`po sessions`, `po retry`, `po watch` all need to find that run dir
again, starting from nothing but the issue id the user typed.

This is the one place that knows the metadata keys, the default set of
log-candidate files, and how to build an error message that tells the
user how to repair missing metadata.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

META_RIG_PATH = "po.rig_path"
META_RUN_DIR = "po.run_dir"

# Prefect-run log glob — mirrors the path referenced in CLAUDE.md and the
# run-log convention. Kept here so dependent verbs share one definition.
PREFECT_LOG_DIR = Path("/tmp/prefect-orchestration-runs")

# Priority-ordered glob patterns inside run_dir. "Priority" only matters
# for tie-break after mtime — freshest wins regardless. Kept as a shared
# list so sibling verbs (`po artifacts`, `po watch`) agree on what counts
# as a log.
RUN_DIR_LOG_GLOBS: tuple[str, ...] = (
    "lint-iter-*.log",
    "test-iter-*.log",
    "e2e-iter-*.log",
    "decision-log.md",
)


class RunDirNotFound(RuntimeError):
    """Bead has no recorded run_dir (metadata missing, bead missing, or bd absent)."""


@dataclass(frozen=True)
class RunLocation:
    rig_path: Path
    run_dir: Path


def _bd_show_json(issue_id: str, *, cwd: Path | str | None = None) -> dict:
    if shutil.which("bd") is None:
        raise RunDirNotFound(
            f"`bd` CLI not on PATH; cannot resolve run_dir for {issue_id}. "
            "Install beads, or pass paths directly to the dependent verb."
        )
    proc = subprocess.run(
        ["bd", "show", issue_id, "--json"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd) if cwd is not None else None,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RunDirNotFound(
            f"`bd show {issue_id} --json` failed: {proc.stderr.strip() or 'no output'}"
        )
    data = json.loads(proc.stdout)
    if isinstance(data, list):
        data = data[0] if data else {}
    return data


def rig_path_from_prefect(issue_id: str) -> Path | None:
    """Find a rig_path by querying Prefect.

    Best-effort thin wrapper around `lookup_prefect_run` that returns
    just the rig_path, kept for compatibility with `po wait`'s simple
    cache. New code should prefer `lookup_prefect_run` directly to also
    pick up the canonical issue_id (handles the UUID-prefix case where
    the user typed a flow-run id instead of the bead id).
    """
    info = lookup_prefect_run(issue_id)
    return info[0] if info else None


def lookup_prefect_run(token: str) -> tuple[Path, str] | None:
    """Resolve a status-table token → `(rig_path, issue_id)`.

    Accepts any of the three forms `po status` shows:

      - bead id          (e.g. `prefect-orchestration-god`)
      - flow-run name    (e.g. `rig-4lp` — `flow_run_name="{issue_id}"`)
      - flow-run UUID    (full or prefix ≥ 4 chars)

    Returns the latest matching flow run's `rig_path` parameter +
    `issue_id` (or `root_id` / `epic_id`) parameter — the canonical bead
    id callers should pass to `bd show`. Returns None on any miss.

    Best-effort: never raises. Lazy-imports prefect so this module stays
    importable when prefect isn't installed.
    """
    if not token:
        return None
    try:
        from prefect.client.orchestration import get_client
        from prefect.client.schemas.filters import (
            FlowRunFilter,
            FlowRunFilterId,
            FlowRunFilterName,
        )
        from prefect.client.schemas.sorting import FlowRunSort
    except Exception:
        return None
    try:
        import anyio
        from uuid import UUID

        # Heuristic: if the token looks like a UUID (or its 8-char prefix),
        # try id-filter; otherwise try name-filter. Fall back to the other
        # if nothing matches.
        looks_like_uuid_prefix = (
            len(token) >= 4 and all(c in "0123456789abcdef-" for c in token.lower())
        )
        try:
            full_uuid = UUID(token)
        except (ValueError, AttributeError):
            full_uuid = None

        async def _run() -> tuple[Path, str] | None:
            async with get_client() as client:
                runs: list = []
                if full_uuid is not None:
                    runs = await client.read_flow_runs(
                        flow_run_filter=FlowRunFilter(
                            id=FlowRunFilterId(any_=[full_uuid]),
                        ),
                        limit=1,
                    )
                if not runs:
                    runs = await client.read_flow_runs(
                        flow_run_filter=FlowRunFilter(
                            name=FlowRunFilterName(any_=[token]),
                        ),
                        sort=FlowRunSort.START_TIME_DESC,
                        limit=1,
                    )
                if not runs and looks_like_uuid_prefix and full_uuid is None:
                    # Prefix-match against recent runs — bounded scan.
                    candidates = await client.read_flow_runs(
                        sort=FlowRunSort.START_TIME_DESC, limit=200,
                    )
                    runs = [r for r in candidates
                            if str(getattr(r, "id", "")).startswith(token)]
                if not runs:
                    return None
                fr = runs[0]
                params = getattr(fr, "parameters", None) or {}
                rp = params.get("rig_path")
                if not rp:
                    return None
                # Canonical bead id: issue_id (e.g. software_dev_full) /
                # root_id (graph_run) / epic_id (epic_run). Fall back to
                # flow-run name (== that id by template).
                bead_id = (
                    params.get("issue_id")
                    or params.get("root_id")
                    or params.get("epic_id")
                    or getattr(fr, "name", None)
                    or token
                )
                return Path(rp), str(bead_id)

        return anyio.run(_run)
    except Exception:
        return None


def resolve_run_dir(issue_id: str) -> RunLocation:
    """Return (rig_path, run_dir) for an issue, or raise RunDirNotFound.

    Lookup order:
      1. `bd show <id>` from cwd  — fastest, the common single-rig case.
      2. Prefect flow run named=<id> → rig_path → retry `bd show <id>`
         in that rig — handles cross-rig dispatch (issue lives in rig A
         but the user is in rig B).

    Requires that the flow has run against the bead at least once since
    the metadata-write infra landed.
    """
    try:
        row = _bd_show_json(issue_id)
    except RunDirNotFound:
        # Fallback: ask Prefect where the flow ran, then retry there.
        # `lookup_prefect_run` returns the canonical bead id too, so the
        # caller can pass a flow-run name OR UUID prefix and we resolve
        # to the right `bd show <issue_id>` lookup.
        info = lookup_prefect_run(issue_id)
        if info is None or not info[0].is_dir():
            raise
        rig_from_prefect, canonical_id = info
        # If the input wasn't actually the bead id (UUID prefix case),
        # use the canonical id for the bd lookup.
        bead_id = canonical_id or issue_id
        row = _bd_show_json(bead_id, cwd=rig_from_prefect)
        # Reflect the resolved id back so downstream code (run_dir paths)
        # uses the canonical name.
        if bead_id != issue_id:
            issue_id = bead_id
    meta = row.get("metadata") or {}
    rig_path_s = meta.get(META_RIG_PATH)
    run_dir_s = meta.get(META_RUN_DIR)
    if not rig_path_s or not run_dir_s:
        raise RunDirNotFound(_missing_metadata_msg(issue_id))
    rig_path = Path(rig_path_s)
    run_dir = Path(run_dir_s)
    if not run_dir.exists():
        raise RunDirNotFound(
            f"run_dir recorded for {issue_id} ({run_dir}) does not exist on disk. "
            "The rig may have been cleaned or moved."
        )
    return RunLocation(rig_path=rig_path, run_dir=run_dir)


def _missing_metadata_msg(issue_id: str) -> str:
    return (
        f"no run_dir recorded for {issue_id}. "
        f"Has `po run <formula> --issue-id {issue_id} ...` been executed? "
        "If the flow ran before this infra change, rerun it, or set manually:\n"
        f"  bd update {issue_id} "
        f"--set-metadata {META_RIG_PATH}=<abs-path> "
        f"--set-metadata {META_RUN_DIR}=<abs-path>"
    )


def candidate_log_files(loc: RunLocation) -> list[Path]:
    """All log-ish files worth showing for this run, unordered.

    Includes Prefect flow logs under /tmp whose mtime falls after the
    run_dir mtime (best-effort — we don't have a stored flow-run id).
    """
    out: list[Path] = []
    run_dir_mtime = loc.run_dir.stat().st_mtime
    if PREFECT_LOG_DIR.is_dir():
        for p in PREFECT_LOG_DIR.glob("*.log"):
            try:
                if p.stat().st_mtime >= run_dir_mtime:
                    out.append(p)
            except OSError:
                continue
    for pattern in RUN_DIR_LOG_GLOBS:
        out.extend(loc.run_dir.glob(pattern))
    return out


def pick_freshest(files: list[Path]) -> Path | None:
    """Max-mtime, alphabetical tie-break, None if list empty."""
    if not files:
        return None
    return max(files, key=lambda p: (p.stat().st_mtime, str(p)))
