"""Repair abandoned PO transport without manufacturing judgment verdicts."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prefect_orchestration import retry, run_lookup, status


@dataclass(frozen=True)
class ReconcileResult:
    inspected: int
    resumed: tuple[str, ...]
    skipped: tuple[str, ...]


def _claim_marker(run_dir: Path, flow_run_id: str) -> Path | None:
    marker = run_dir / f".po-reconciled-{flow_run_id}"
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return None
    os.close(fd)
    return marker


async def _find_abandoned(client: Any, stale_secs: int) -> list[tuple[str, str]]:
    # Prefect caps each filter response at 200. Page until exhaustion so old
    # Running zombies cannot crowd recoverable work out of reconciliation.
    runs: list[Any] = []
    offset = 0
    while True:
        page = await status.find_runs_by_issue_id(
            client, state="Running", limit=200, offset=offset
        )
        runs.extend(page)
        if len(page) < 200:
            break
        offset += len(page)
    groups = status.group_by_issue(runs)
    abandoned: list[tuple[str, str]] = []
    for group in groups:
        params = getattr(group.latest, "parameters", None) or {}
        rig_path = Path(params["rig_path"]) if params.get("rig_path") else None
        age = status.compute_stale_secs(group.issue_id, rig_path)
        if age is None or age < stale_secs or status._has_live_process(group.issue_id):
            continue
        abandoned.append((group.issue_id, str(group.latest.id)))
    return abandoned


def reconcile_once(
    *, stale_secs: int = status.PO_WATCHDOG_STALE_SECS
) -> ReconcileResult:
    """Fail and durably resume abandoned controllers exactly once."""
    import anyio
    from prefect.client.orchestration import get_client
    from prefect.states import Failed

    async def discover_and_fail() -> list[tuple[str, str]]:
        async with get_client() as client:
            abandoned = await _find_abandoned(client, stale_secs)
            for _issue_id, flow_run_id in abandoned:
                await client.set_flow_run_state(
                    flow_run_id,
                    Failed(message="PO controller abandoned; durable resume submitted"),
                    force=True,
                )
            return abandoned

    abandoned = anyio.run(discover_and_fail)
    resumed: list[str] = []
    skipped: list[str] = []
    for issue_id, flow_run_id in abandoned:
        try:
            loc = run_lookup.resolve_run_dir(issue_id)
            marker = _claim_marker(loc.run_dir, flow_run_id)
            if marker is None:
                skipped.append(issue_id)
                continue
            formula = retry._resolve_formula(
                loc.run_dir, issue_id, None, lambda _: None
            )
            from prefect_orchestration.resume import resume_issue

            resume_issue(issue_id, force=True, formula=formula, when=None)
            resumed.append(issue_id)
        except Exception:
            skipped.append(issue_id)
            try:
                marker.unlink()  # type: ignore[possibly-undefined]
            except (NameError, OSError):
                pass
    return ReconcileResult(
        inspected=len(abandoned), resumed=tuple(resumed), skipped=tuple(skipped)
    )
