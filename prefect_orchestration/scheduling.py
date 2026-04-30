"""Future-scheduled `po run` — `--time <when>` plumbing.

Backs `po run <formula> --time <when>` (issue prefect-orchestration-7jr):
turn a synchronous in-process flow invocation into a one-shot scheduled
flow-run on the connected Prefect server, by convention against the
`<formula>-manual` deployment.

Pure helper module — no Typer imports — so unit tests can exercise the
parser and the deployment-lookup seam without dragging the CLI graph in.
The CLI imports + composes these helpers in `cli.py::run`.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

_REL_RE = re.compile(r"^\+?(\d+)\s*([smhdw])$", re.IGNORECASE)
_REL_UNIT = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}


def parse_when(spec: str) -> datetime:
    """Parse `--time` to a tz-aware UTC datetime.

    Accepts a relative duration (`2h`, `30m`, `+30m`, `1d`) or an
    ISO-8601 string with timezone (`2026-04-25T09:00:00-04:00`, `…Z`).
    Naive ISO datetimes are rejected: silently picking UTC for
    "schedule at 09:00" would surprise users in non-UTC timezones.
    Relative durations must be > 0 and resolve to `now + delta` at
    parse time.

    Raises ValueError on bad input; the message quotes the user's
    exact spec so the CLI can surface it verbatim.
    """
    if not spec or not spec.strip():
        raise ValueError("empty --time value")
    spec = spec.strip()
    m = _REL_RE.match(spec)
    if m:
        n = int(m.group(1))
        if n == 0:
            raise ValueError(f"--time {spec!r}: relative duration must be > 0")
        unit = _REL_UNIT[m.group(2).lower()]
        return datetime.now(timezone.utc) + timedelta(**{unit: n})
    iso = spec[:-1] + "+00:00" if spec.endswith("Z") else spec
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError as exc:
        raise ValueError(
            f"bad --time {spec!r}: expected relative (2h, 30m, 1d, +30m) "
            "or ISO-8601 with timezone"
        ) from exc
    if dt.tzinfo is None:
        raise ValueError(
            f"--time {spec!r}: ISO-8601 must include a timezone offset "
            "(e.g. +00:00 or Z); naive datetimes are rejected to avoid "
            "ambiguous local-vs-UTC scheduling."
        )
    return dt.astimezone(timezone.utc)


class ManualDeploymentMissing(Exception):
    """Raised when `<formula>-manual` is not on the Prefect server."""

    def __init__(self, formula: str) -> None:
        self.formula = formula
        # Build a copy-pasteable register() snippet using the formula's
        # snake_case form for the Python identifier (the user's flow
        # callable in their pack module).
        callable_hint = formula.replace("-", "_")
        super().__init__(
            f"formula {formula!r} has no manual deployment on the "
            f"connected Prefect server. Expected deployment name: "
            f"{formula}-manual.\n"
            f"  Fix: register one in your pack and apply, e.g.\n"
            f"    # <your-pack>/deployments.py\n"
            f"    def register():\n"
            f'        return [{callable_hint}.to_deployment(name="{formula}-manual")]\n'
            f"  then run: po deploy --apply"
        )


async def find_manual_deployment(client: Any, formula: str) -> Any | None:
    """Return the `<formula>-manual` deployment from the Prefect server, or None.

    Single round-trip via DeploymentFilter. We deliberately query the
    server, not the pack-side `load_deployments()` output, because the
    latter only describes what *would* be applied — server state is the
    source of truth for what's actually schedulable right now.
    """
    from prefect.client.schemas.filters import (
        DeploymentFilter,
        DeploymentFilterName,
    )

    name = f"{formula}-manual"
    deployments = await client.read_deployments(
        deployment_filter=DeploymentFilter(name=DeploymentFilterName(any_=[name])),
        limit=2,
    )
    return deployments[0] if deployments else None


async def submit_scheduled_run(
    *,
    client: Any,
    formula: str,
    parameters: dict[str, Any],
    scheduled_time: datetime,
    issue_id: str | None = None,
) -> tuple[Any, str]:
    """Submit a one-shot scheduled flow-run for `<formula>-manual`.

    Returns `(flow_run, full_name)` where `full_name` is the
    `<flow_name>/<deployment_name>` form used by `prefect deployment run`.

    `timeout=0` makes `arun_deployment` return as soon as the flow-run
    is created in `Scheduled` state — the worker picks it up at
    `scheduled_time`. `as_subflow=False` is required because `po run`
    runs outside any Prefect flow context.
    """
    deployment = await find_manual_deployment(client, formula)
    if deployment is None:
        raise ManualDeploymentMissing(formula)
    flow = await client.read_flow(deployment.flow_id)
    full_name = f"{flow.name}/{deployment.name}"

    from prefect.deployments.flow_runs import arun_deployment

    tags = [f"issue_id:{issue_id}"] if issue_id else None
    flow_run = await arun_deployment(
        full_name,
        parameters=parameters,
        scheduled_time=scheduled_time,
        timeout=0,
        as_subflow=False,
        tags=tags,
    )
    return flow_run, full_name
