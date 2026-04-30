"""Future-scheduled `po run` — `--at <when>` plumbing.

Backs `po run <formula> --at <when>` (issue prefect-orchestration-7jr,
renamed --time → --at in prefect-orchestration-40y):
turn a synchronous in-process flow invocation into a one-shot scheduled
flow-run on the connected Prefect server, by convention against the
`<formula>-manual` deployment.

Pure helper module — no Typer imports — so unit tests can exercise the
parser and the deployment-lookup seam without dragging the CLI graph in.
The CLI imports + composes these helpers in `cli.py::run`.
"""

from __future__ import annotations

import re
import sys
import warnings
from datetime import datetime, timedelta, timezone
from typing import Any


def _load_formula_flow(formula: str) -> Any | None:
    """Return the po.formulas flow object for `formula`, or None."""
    from importlib.metadata import entry_points

    try:
        eps = entry_points(group="po.formulas")
    except TypeError:
        eps = entry_points().get("po.formulas", [])  # type: ignore[union-attr]
    for ep in eps:
        if ep.name == formula:
            try:
                return ep.load()
            except Exception:
                return None
    return None

_REL_RE = re.compile(r"^\+?(\d+)\s*([smhdw])$", re.IGNORECASE)
_REL_UNIT = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}


def parse_when(spec: str) -> datetime:
    """Parse `--at` to a tz-aware UTC datetime.

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
        raise ValueError("empty --at value")
    spec = spec.strip()
    m = _REL_RE.match(spec)
    if m:
        n = int(m.group(1))
        if n == 0:
            raise ValueError(f"--at {spec!r}: relative duration must be > 0")
        unit = _REL_UNIT[m.group(2).lower()]
        return datetime.now(timezone.utc) + timedelta(**{unit: n})
    iso = spec[:-1] + "+00:00" if spec.endswith("Z") else spec
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError as exc:
        raise ValueError(
            f"bad --at {spec!r}: expected relative (2h, 30m, 1d, +30m) "
            "or ISO-8601 with timezone"
        ) from exc
    if dt.tzinfo is None:
        raise ValueError(
            f"--at {spec!r}: ISO-8601 must include a timezone offset "
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


async def ensure_manual_deployment(client: Any, formula: str) -> tuple[Any, str | None]:
    """Return (deployment, worker_warning | None). Auto-applies if absent.

    If `<formula>-manual` is not on the server, loads pack-declared
    deployments and applies any matching one. Raises `ManualDeploymentMissing`
    if the deployment is still absent after apply (pack doesn't register it).
    After finding/creating the deployment, probes the work pool for running
    workers and returns a warning string if none are found.
    """
    import asyncio

    from prefect_orchestration import deployments as _deployments

    deployment = await find_manual_deployment(client, formula)
    if deployment is None:
        # First fallback: auto-apply from pack-declared deployments.
        loaded, _errors = _deployments.load_deployments()
        target_name = f"{formula}-manual"
        matches = [
            d for d in loaded if getattr(d.deployment, "name", None) == target_name
        ]
        if matches:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                for item in matches:
                    await asyncio.to_thread(
                        _deployments.apply_deployment, item.deployment
                    )
        deployment = await find_manual_deployment(client, formula)

    if deployment is None:
        # Second fallback: auto-create from the formula's flow object.
        flow_obj = _load_formula_flow(formula)
        if flow_obj is not None:
            target_name = f"{formula}-manual"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                runner_dep = await asyncio.to_thread(
                    lambda: flow_obj.to_deployment(name=target_name)
                )
                await asyncio.to_thread(_deployments.apply_deployment, runner_dep)
            print(f"auto-created deployment: {target_name}", file=sys.stderr)
            deployment = await find_manual_deployment(client, formula)
        if deployment is None:
            raise ManualDeploymentMissing(formula)

    # Probe for running workers on the target pool.
    warn_msg: str | None = None
    pool_name = getattr(deployment, "work_pool_name", None)
    if pool_name:
        try:
            workers = await client.read_workers(work_pool_name=pool_name)
            if len(workers) == 0:
                warn_msg = (
                    f"warning: no workers running on pool {pool_name!r}. "
                    f"Run `prefect worker start --pool {pool_name}` before "
                    f"the scheduled time or the run will stay Scheduled."
                )
        except Exception:  # noqa: BLE001 — older Prefect servers may lack this API
            pass

    return deployment, warn_msg


async def submit_scheduled_run(
    *,
    client: Any,
    formula: str,
    parameters: dict[str, Any],
    scheduled_time: datetime,
    issue_id: str | None = None,
    job_variables: dict[str, Any] | None = None,
) -> tuple[Any, str, str | None]:
    """Submit a one-shot scheduled flow-run for `<formula>-manual`.

    Returns `(flow_run, full_name, worker_warning | None)` where
    `full_name` is the `<flow_name>/<deployment_name>` form used by
    `prefect deployment run`. Auto-applies the deployment if absent.

    `timeout=0` makes `arun_deployment` return as soon as the flow-run
    is created in `Scheduled` state — the worker picks it up at
    `scheduled_time`. `as_subflow=False` is required because `po run`
    runs outside any Prefect flow context.
    """
    deployment, warn_msg = await ensure_manual_deployment(client, formula)
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
        job_variables=job_variables,
    )
    return flow_run, full_name, warn_msg
