"""Repair abandoned PO transport without manufacturing judgment verdicts."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from prefect_orchestration import retry, run_lookup, status


@dataclass(frozen=True)
class ReconcileResult:
    inspected: int
    resumed: tuple[str, ...]
    terminalized: tuple[str, ...]
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
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    while True:
        page = await status.find_runs_by_issue_id(
            client, state="Running", since=since, limit=200, offset=offset
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


async def _find_old_zombies(
    client: Any, *, older_than: timedelta = timedelta(hours=24)
) -> list[tuple[str, str]]:
    """Find ancient Running controllers that cannot still be legitimate turns.

    Recent controllers use artifact staleness and may be resumed. This separate
    all-history pass only terminalizes records older than the declared policy
    boundary and never submits work, so old Prefect rows cannot remain Running
    forever merely because their temporary run directory disappeared.
    """
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

    cutoff = datetime.now(timezone.utc) - older_than
    zombies: list[tuple[str, str]] = []
    for group in status.group_by_issue(runs):
        started = next(
            (
                value
                for value in (
                    getattr(group.latest, "expected_start_time", None),
                    getattr(group.latest, "start_time", None),
                    getattr(group.latest, "created", None),
                )
                if value is not None
            ),
            None,
        )
        if started is None:
            continue
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if started > cutoff or status._has_live_process(group.issue_id):
            continue
        zombies.append((group.issue_id, str(group.latest.id)))
    return zombies


def reconcile_once(
    *, stale_secs: int = status.PO_WATCHDOG_STALE_SECS
) -> ReconcileResult:
    """Fail and durably resume abandoned controllers exactly once."""
    import anyio
    from prefect.client.orchestration import get_client
    from prefect.states import Failed

    async def discover_and_fail() -> tuple[
        list[tuple[str, str]], list[tuple[str, str]]
    ]:
        async with get_client() as client:
            abandoned = await _find_abandoned(client, stale_secs)
            zombies = await _find_old_zombies(client)
            abandoned_run_ids = {flow_run_id for _issue_id, flow_run_id in abandoned}
            zombies = [row for row in zombies if row[1] not in abandoned_run_ids]
            for _issue_id, flow_run_id in abandoned:
                await client.set_flow_run_state(
                    flow_run_id,
                    Failed(message="PO controller abandoned; durable resume submitted"),
                    force=True,
                )
            for _issue_id, flow_run_id in zombies:
                await client.set_flow_run_state(
                    flow_run_id,
                    Failed(
                        message="PO controller zombie terminalized; no resume submitted"
                    ),
                    force=True,
                )
            return abandoned, zombies

    abandoned, zombies = anyio.run(discover_and_fail)
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
        inspected=len(abandoned) + len(zombies),
        resumed=tuple(resumed),
        terminalized=tuple(issue_id for issue_id, _flow_run_id in zombies),
        skipped=tuple(skipped),
    )
