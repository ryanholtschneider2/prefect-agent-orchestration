"""Prefect flow-run inspection grouped by beads `issue_id:<id>` tag.

PO flows stamp each run with `issue_id:<id>` (and `epic_run` adds
`epic_id:<id>`). This module queries the Prefect server, groups runs by
bead, and derives the "current step" from the latest non-terminal task
run. Factored out of the CLI so `po watch` (`prefect-orchestration-zrk`)
can reuse `find_runs_by_issue_id`.

Keep this module free of Typer imports — it's the reusable seam.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ISSUE_TAG_PREFIX = "issue_id:"
EPIC_TAG_PREFIX = "epic_id:"

_REL_RE = re.compile(r"^(\d+)\s*([smhdw])$", re.IGNORECASE)
_REL_UNIT = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}

# Prefect terminal state types — anything else is "still running" for the
# purposes of deriving a "current step".
_TERMINAL_STATES = {"COMPLETED", "FAILED", "CRASHED", "CANCELLED", "CANCELLING"}


def parse_since(spec: str) -> datetime:
    """Parse `--since` — `Nh`/`Nm`/`Nd`/`Nw`/`Ns` or ISO-8601 → aware UTC datetime.

    Raises ValueError on bad input.
    """
    if not spec:
        raise ValueError("empty --since value")
    m = _REL_RE.match(spec.strip())
    if m:
        n = int(m.group(1))
        unit = _REL_UNIT[m.group(2).lower()]
        return datetime.now(timezone.utc) - timedelta(**{unit: n})
    # ISO-8601: accept trailing 'Z'.
    iso = spec.strip()
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError as exc:
        raise ValueError(
            f"bad --since {spec!r}: expected relative (1h, 30m, 2d) or ISO-8601"
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def extract_issue_id(tags: Iterable[str]) -> str | None:
    """Return the first `issue_id:<id>` value from a tag list, or None."""
    for t in tags or ():
        if t.startswith(ISSUE_TAG_PREFIX):
            return t[len(ISSUE_TAG_PREFIX) :]
    return None


@dataclass
class IssueGroup:
    """One bead's worth of flow runs, newest first."""

    issue_id: str
    latest: Any  # FlowRun (Prefect client schema) — duck-typed in tests
    extras: list[Any]
    current_step: str | None = None
    stale_secs: int | None = None  # seconds since last run-dir write; None if unknown

    @property
    def extra_count(self) -> int:
        return len(self.extras)


def group_by_issue(flow_runs: Iterable[Any]) -> list[IssueGroup]:
    """Group flow runs by `issue_id:<id>` tag, latest-first per issue.

    Runs without an `issue_id:` tag are dropped. "Latest" is by
    `expected_start_time` if set, else `created`, else `start_time`.
    """

    def _sort_key(fr: Any) -> datetime:
        for attr in ("expected_start_time", "start_time", "created"):
            val = getattr(fr, attr, None)
            if val is not None:
                return val  # type: ignore[no-any-return]
        return datetime.min.replace(tzinfo=timezone.utc)

    buckets: dict[str, list[Any]] = {}
    for fr in flow_runs:
        iid = extract_issue_id(getattr(fr, "tags", []) or [])
        if not iid:
            continue
        buckets.setdefault(iid, []).append(fr)

    groups: list[IssueGroup] = []
    for iid, runs in buckets.items():
        runs.sort(key=_sort_key, reverse=True)
        groups.append(IssueGroup(issue_id=iid, latest=runs[0], extras=runs[1:]))

    groups.sort(key=lambda g: _sort_key(g.latest), reverse=True)
    return groups


def current_step(task_runs: Iterable[Any]) -> str | None:
    """Pick the most-recent non-terminal task-run name, else the latest."""

    def _sort_key(tr: Any) -> datetime:
        for attr in ("start_time", "expected_start_time", "created"):
            val = getattr(tr, attr, None)
            if val is not None:
                return val  # type: ignore[no-any-return]
        return datetime.min.replace(tzinfo=timezone.utc)

    runs = list(task_runs)
    if not runs:
        return None
    runs.sort(key=_sort_key, reverse=True)
    for tr in runs:
        state = getattr(tr, "state_type", None)
        state_str = getattr(state, "value", state)
        if state_str and str(state_str).upper() not in _TERMINAL_STATES:
            return getattr(tr, "name", None)
    return getattr(runs[0], "name", None)


async def find_runs_by_issue_id(
    client: Any,
    *,
    issue_id: str | None = None,
    since: datetime | None = None,
    state: str | None = None,
    limit: int = 200,
) -> list[Any]:
    """Query Prefect server for flow runs, optionally filtered.

    `client` is an `PrefectClient` (from `get_client()`). Tag filtering:
    if `issue_id` is given we filter server-side via `tags.all_`; otherwise
    we pull up to `limit` recent runs and filter client-side for any
    `issue_id:` tag, which keeps the API single-round-trip.
    """
    from prefect.client.schemas.filters import (
        FlowRunFilter,
        FlowRunFilterExpectedStartTime,
        FlowRunFilterStateName,
        FlowRunFilterTags,
    )
    from prefect.client.schemas.sorting import FlowRunSort

    kwargs: dict[str, Any] = {}
    if issue_id is not None:
        kwargs["tags"] = FlowRunFilterTags(all_=[f"{ISSUE_TAG_PREFIX}{issue_id}"])
    if since is not None:
        # expected_start_time is always set at dispatch time (unlike start_time,
        # which is null until the flow actually starts). Using it here ensures
        # newly dispatched PENDING/SCHEDULED runs are visible immediately.
        kwargs["expected_start_time"] = FlowRunFilterExpectedStartTime(after_=since)
    if state is not None:
        kwargs["state"] = {"name": FlowRunFilterStateName(any_=[state])}

    flow_run_filter = FlowRunFilter(**kwargs) if kwargs else None

    runs = await client.read_flow_runs(
        flow_run_filter=flow_run_filter,
        sort=FlowRunSort.EXPECTED_START_TIME_DESC,
        limit=limit,
    )
    if issue_id is None:
        runs = [r for r in runs if extract_issue_id(getattr(r, "tags", []) or [])]
    return list(runs)


async def current_step_for_flow_run(client: Any, flow_run_id: Any) -> str | None:
    """Return the role/task name of the flow run's active step (or last)."""
    from prefect.client.schemas.filters import FlowRunFilter
    from prefect.client.schemas.sorting import TaskRunSort

    task_runs = await client.read_task_runs(
        flow_run_filter=FlowRunFilter(id={"any_": [flow_run_id]}),
        sort=TaskRunSort.EXPECTED_START_TIME_DESC,
        limit=50,
    )
    return current_step(task_runs)


# ─── formatting ──────────────────────────────────────────────────────


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def _fmt_duration(start: datetime | None, end: datetime | None) -> str:
    if start is None:
        return "-"
    finish = end or datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if finish.tzinfo is None:
        finish = finish.replace(tzinfo=timezone.utc)
    secs = int((finish - start).total_seconds())
    if secs < 0:
        return "-"
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m{secs % 60:02d}s"
    h, rem = divmod(secs, 3600)
    return f"{h}h{rem // 60:02d}m"


def _rig_label(fr: Any) -> str:
    """Best-effort rig label from a flow run's `rig` / `rig_path` parameter.

    Falls back to "-" when neither is set (ad-hoc / scratch flows).
    """
    params = getattr(fr, "parameters", None) or {}
    rig = params.get("rig")
    if isinstance(rig, str) and rig:
        return rig
    rp = params.get("rig_path")
    if isinstance(rp, str) and rp:
        from pathlib import Path as _P

        return _P(rp).name or "-"
    return "-"


def _flow_run_id_short(fr: Any) -> str:
    """First 8 chars of the flow-run UUID — enough to grep /
    `prefect flow-run inspect <prefix>` in practice."""
    fr_id = getattr(fr, "id", None)
    if fr_id is None:
        return "-"
    s = str(fr_id)
    return s.split("-", 1)[0] if "-" in s else s[:8]


def _is_zombie(fr: Any) -> bool:
    """A flow is a 'zombie' if it's in a Running state but its rig_path
    parameter points at a directory that no longer exists on disk.

    Common cause: a pytest fixture spawned a Prefect flow against a
    `tmp_path`-style rig, then its process died (kill, timeout, etc.)
    without transitioning the flow to a terminal state. The temp dir
    got cleaned up, so the flow run is "Running" forever in the DB but
    can't make progress. Hiding these by default keeps `po status` from
    drowning real runs.
    """
    state = getattr(fr, "state_name", None) or getattr(
        getattr(fr, "state", None), "name", None
    )
    if not state or str(state).lower() != "running":
        return False
    params = getattr(fr, "parameters", None) or {}
    rp = params.get("rig_path")
    if not rp:
        return False
    from pathlib import Path as _P

    return not _P(rp).exists()


PO_WATCHDOG_STALE_SECS: int = int(os.environ.get("PO_WATCHDOG_STALE_SECS", "600"))
_STALE_WARN_SECS: int = 300  # show (stale: Nm) annotation before auto-failing


def _run_dir_max_mtime(run_dir: Path) -> float | None:
    """Max mtime across all files under run_dir. None if no files exist."""
    mtimes = [p.stat().st_mtime for p in run_dir.rglob("*") if p.is_file()]
    return max(mtimes) if mtimes else None


def _has_live_process(issue_id: str) -> bool:
    """True if any tmux session or pgrep match for issue_id exists."""
    safe = issue_id.replace(".", "_")
    prefix = f"po-{safe}-"
    if shutil.which("tmux"):
        r = subprocess.run(["tmux", "ls"], capture_output=True, text=True, check=False)
        if r.returncode == 0 and prefix in r.stdout:
            return True
    if shutil.which("pgrep"):
        r = subprocess.run(["pgrep", "-f", issue_id], capture_output=True, check=False)
        if r.returncode == 0:
            return True
    return False


def compute_stale_secs(issue_id: str) -> int | None:
    """Seconds since last run-dir write via bead metadata. None if not computable."""
    from prefect_orchestration.run_lookup import RunDirNotFound, _bd_show_json

    try:
        row = _bd_show_json(issue_id)
    except (RunDirNotFound, Exception):
        return None
    run_dir_s = (row.get("metadata") or {}).get("po.run_dir")
    if not run_dir_s:
        return None
    run_dir = Path(run_dir_s)
    if not run_dir.exists():
        return None
    mtime = _run_dir_max_mtime(run_dir)
    if mtime is None:
        return None
    return int(time.time() - mtime)


async def watchdog_fail_stale_runs(
    client: Any,
    groups: list[IssueGroup],
    *,
    fail_after_secs: int = PO_WATCHDOG_STALE_SECS,
) -> list[str]:
    """Auto-Fail RUNNING flows that are stale + have no live process.

    Returns list of issue_ids that were failed. Best-effort; never raises.
    """
    from prefect.states import Failed as PrefectFailed

    failed_ids: list[str] = []
    for g in groups:
        state = (getattr(g.latest, "state_name", None) or "").lower()
        if state != "running":
            continue
        stale = g.stale_secs
        if stale is None or stale < fail_after_secs:
            continue
        if _has_live_process(g.issue_id):
            continue
        # Both stale + no live process: mark as Failed.
        try:
            await client.set_flow_run_state(
                g.latest.id,
                PrefectFailed(message="Worker silent kill (watchdog)"),
                force=True,
            )
            subprocess.run(
                ["bd", "update", g.issue_id, "--assignee", ""],
                capture_output=True,
                text=True,
                check=False,
            )
            failed_ids.append(g.issue_id)
        except Exception:  # noqa: BLE001 — best-effort; po status always exits 0
            pass
    return failed_ids


def partition_zombies(groups: list[IssueGroup]) -> tuple[list[IssueGroup], int]:
    """Split groups into (live, zombie_count). Zombies are 'Running' rows
    whose rig_path no longer exists on disk — usually leaked pytest runs."""
    live: list[IssueGroup] = []
    hidden = 0
    for g in groups:
        if _is_zombie(g.latest):
            hidden += 1
        else:
            live.append(g)
    return live, hidden


def to_json_list(groups: list[IssueGroup]) -> list[dict]:
    """Stable JSON shape for `po status --json`."""
    rows = []
    for g in groups:
        fr = g.latest
        state = (
            getattr(fr, "state_name", None)
            or getattr(getattr(fr, "state", None), "name", None)
            or "-"
        )
        start = getattr(fr, "start_time", None) or getattr(
            fr, "expected_start_time", None
        )
        end = getattr(fr, "end_time", None)
        rows.append(
            {
                "issue_id": g.issue_id,
                "rig": _rig_label(fr),
                "run_id": _flow_run_id_short(fr),
                "flow_name": str(
                    getattr(fr, "name", None) or getattr(fr, "flow_name", "-")
                ),
                "state": str(state),
                "started": start.isoformat() if start is not None else None,
                "ended": end.isoformat() if end is not None else None,
                "current_step": g.current_step,
                "run_count": 1 + g.extra_count,
                "stale_secs": g.stale_secs,
            }
        )
    return rows


def render_table(groups: list[IssueGroup]) -> str:
    """Format grouped runs as a plain-text table.

    Columns:
      ISSUE     — value of the `issue_id:<id>` tag (canonical bead id)
      RIG       — `rig` param if set, else basename of `rig_path`
      RUN       — first 8 chars of the flow-run UUID (lookup with
                  `prefect flow-run inspect <prefix>` or in the UI)
      FLOW      — flow-run name (often == ISSUE; differs for graph_run
                  where it's `{root_id}` and for ad-hoc flows)
      STATE / STARTED / DURATION / STEP / RUNS — Prefect-side metadata
    """
    if not groups:
        return "no flow runs with issue_id tag found."
    headers = (
        "ISSUE",
        "RIG",
        "RUN",
        "FLOW",
        "STATE",
        "STARTED",
        "DURATION",
        "STEP",
        "RUNS",
    )
    rows: list[tuple[str, ...]] = []
    for g in groups:
        fr = g.latest
        state = (
            getattr(fr, "state_name", None)
            or getattr(getattr(fr, "state", None), "name", "-")
            or "-"
        )
        start = getattr(fr, "start_time", None) or getattr(
            fr, "expected_start_time", None
        )
        end = getattr(fr, "end_time", None)
        state_str = str(state)
        if g.stale_secs is not None and g.stale_secs >= _STALE_WARN_SECS:
            mins = g.stale_secs // 60
            state_str = f"{state_str} (stale: {mins}m)"
        rows.append(
            (
                g.issue_id,
                _rig_label(fr),
                _flow_run_id_short(fr),
                str(getattr(fr, "name", None) or getattr(fr, "flow_name", "-")),
                state_str,
                _fmt_dt(start),
                _fmt_duration(start, end),
                g.current_step or "-",
                str(1 + g.extra_count),
            )
        )
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
    for row in rows:
        lines.append(fmt.format(*row))
    return "\n".join(lines)
