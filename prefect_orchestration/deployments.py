"""Discovery + apply for Prefect deployments declared by formula packs.

Packs register a `register()` callable under the `po.deployments` entry-point
group. The callable returns one `RunnerDeployment` (built via
`flow.to_deployment(...)`) or a list of them. Core stays flow-agnostic.

    # pack pyproject.toml
    [project.entry-points."po.deployments"]
    software-dev = "po_formulas.deployments:register"

    # pack module
    def register():
        return [epic_run.to_deployment(name="nightly", schedule=Cron("0 9 * * *"))]
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any


@dataclass
class LoadedDeployment:
    """Pairs a deployment with the pack name (EP name) that produced it."""

    pack: str
    deployment: Any  # prefect.deployments.runner.RunnerDeployment


@dataclass
class LoadError:
    pack: str
    error: str


def _iter_entry_points() -> list[Any]:
    try:
        return list(entry_points(group="po.deployments"))
    except TypeError:
        return list(entry_points().get("po.deployments", []))  # type: ignore[attr-defined]


def load_deployments() -> tuple[list[LoadedDeployment], list[LoadError]]:
    """Discover every registered deployment.

    Returns (loaded, errors). A pack that raises during `register()` is
    reported as an error but does not abort the rest.
    """
    loaded: list[LoadedDeployment] = []
    errors: list[LoadError] = []
    for ep in _iter_entry_points():
        try:
            register_fn = ep.load()
        except Exception as exc:
            errors.append(LoadError(pack=ep.name, error=f"load failed: {exc}"))
            continue
        if not callable(register_fn):
            errors.append(
                LoadError(pack=ep.name, error=f"entry point is not callable: {register_fn!r}")
            )
            continue
        try:
            result = register_fn()
        except Exception as exc:
            errors.append(LoadError(pack=ep.name, error=f"register() raised: {exc}"))
            continue
        # Normalize single-vs-list. Accept any iterable of deployments.
        if result is None:
            continue
        if isinstance(result, (list, tuple)):
            items = list(result)
        else:
            items = [result]
        for dep in items:
            loaded.append(LoadedDeployment(pack=ep.name, deployment=dep))
    return loaded, errors


def format_schedule(deployment: Any) -> str:
    """Render a deployment's schedule in one line for the listing.

    Handles Prefect 3 `RunnerDeployment` (`.schedules: list[DeploymentScheduleCreate]`),
    each wrapping a `CronSchedule`, `IntervalSchedule`, or `RRuleSchedule`.
    """
    schedules = getattr(deployment, "schedules", None) or []
    if not schedules:
        # Prefect 2 fallback
        single = getattr(deployment, "schedule", None)
        if single is None:
            return "manual"
        schedules = [single]
    parts: list[str] = []
    for entry in schedules:
        sched = getattr(entry, "schedule", entry)
        cls = type(sched).__name__
        if cls == "CronSchedule":
            tz = getattr(sched, "timezone", None)
            tz_str = f" {tz}" if tz else ""
            parts.append(f"cron({sched.cron}{tz_str})")
        elif cls == "IntervalSchedule":
            interval = getattr(sched, "interval", None)
            parts.append(f"interval({interval})")
        elif cls == "RRuleSchedule":
            parts.append(f"rrule({getattr(sched, 'rrule', '?')})")
        else:
            parts.append(cls)
    return ", ".join(parts) if parts else "manual"


def apply_deployment(deployment: Any, work_pool: str | None = None) -> str:
    """Apply a single deployment to the configured Prefect server.

    `RunnerDeployment.apply()` upserts by (flow_name, name), so re-running is
    idempotent. Returns the deployment ID as a string.
    """
    if work_pool is not None:
        # Set before apply so the server records the pool.
        deployment.work_pool_name = work_pool
    deployment_id = deployment.apply()
    return str(deployment_id)


def prefect_api_configured() -> bool:
    """Best-effort check that a Prefect API target is set.

    `--apply` needs a reachable Prefect server. `PREFECT_API_URL` is the
    standard env var; if unset Prefect falls back to an ephemeral SQLite DB
    which is almost never what the user wants for `po deploy --apply`.
    """
    return bool(os.environ.get("PREFECT_API_URL"))
