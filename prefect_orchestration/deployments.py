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

import logging
import os
import tomllib
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class LoadedDeployment:
    """Pairs a deployment with the pack name (EP name) that produced it."""

    pack: str
    deployment: Any  # prefect.deployments.runner.RunnerDeployment


@dataclass
class LoadError:
    pack: str
    error: str


def _iter_group_entry_points(group: str) -> list[Any]:
    try:
        return list(entry_points(group=group))
    except TypeError:
        return list(entry_points().get(group, []))  # type: ignore[attr-defined]


def _iter_entry_points() -> list[Any]:
    return _iter_group_entry_points("po.deployments")


def iter_formula_entry_points() -> list[Any]:
    """Return all registered `po.formulas` entry points."""
    return _iter_group_entry_points("po.formulas")


def load_formula_flows(
    *, skip_names: set[str] | frozenset[str] | None = None
) -> tuple[dict[str, Any], list[LoadError]]:
    """Load `po.formulas` callables keyed by entry-point name.

    Returns `(flows_by_name, errors)`. A formula pack that fails to load is
    reported as an error but does not abort the rest.
    """
    flows: dict[str, Any] = {}
    errors: list[LoadError] = []
    skipped = skip_names or set()
    for ep in iter_formula_entry_points():
        if ep.name in skipped:
            continue
        try:
            flows[ep.name] = ep.load()
        except Exception as exc:
            errors.append(LoadError(pack=ep.name, error=f"load failed: {exc}"))
    return flows, errors


def build_cron_deployments_from_order_dir(
    orders_dir: Path,
    *,
    tag_prefix: str,
    default_timezone: str = "UTC",
    work_pool_name: str = "po",
    log: logging.Logger | None = None,
) -> list[Any]:
    """Build Prefect cron deployments from flat TOML files in `orders_dir`.

    Schema:
      cron = "<cron expr>"           # required
      formula = "<po.formulas EP>"   # required
      description = "..."            # optional
      tags = ["a", "b"]              # optional; default: [tag_prefix, <formula>]
      timezone = "UTC"               # optional; default: `default_timezone`
      [params]                       # optional table -> parameters=
      key = value
    """
    active_logger = log or logger
    try:
        from prefect.client.schemas.schedules import CronSchedule
        from prefect.deployments.runner import EntrypointType
    except Exception as exc:  # pragma: no cover - prefect missing == bigger problem
        active_logger.warning("prefect unavailable; skipping deployments (%s)", exc)
        return []

    flows_by_name, load_errors = load_formula_flows()
    for err in load_errors:
        active_logger.warning("failed to load formula %r: %s", err.pack, err.error)

    deployments: list[Any] = []
    for toml_path in sorted(orders_dir.glob("*.toml")):
        try:
            with toml_path.open("rb") as fh:
                spec = tomllib.load(fh)
        except Exception as exc:
            active_logger.warning("failed to parse %s: %s", toml_path.name, exc)
            continue
        cron = spec.get("cron")
        formula_name = spec.get("formula")
        if not cron or not formula_name:
            active_logger.warning(
                "%s missing required keys (cron, formula); skipping", toml_path.name
            )
            continue
        flow_obj = flows_by_name.get(formula_name)
        if flow_obj is None:
            active_logger.warning(
                "formula %r referenced by %s is not registered; skipping",
                formula_name,
                toml_path.name,
            )
            continue
        params = spec.get("params") or {}
        if not isinstance(params, dict):
            active_logger.warning(
                "%s [params] must be a table; got %r",
                toml_path.name,
                type(params).__name__,
            )
            continue
        to_deployment = getattr(flow_obj, "to_deployment", None)
        if not callable(to_deployment):
            active_logger.warning(
                "formula %r referenced by %s is not a Prefect flow; skipping",
                formula_name,
                toml_path.name,
            )
            continue
        try:
            deployments.append(
                to_deployment(
                    name=toml_path.stem,
                    schedule=CronSchedule(
                        cron=cron, timezone=spec.get("timezone", default_timezone)
                    ),
                    tags=spec.get("tags", [tag_prefix, formula_name]),
                    description=spec.get(
                        "description",
                        f"{formula_name} cron deployment "
                        f"({cron} {spec.get('timezone', default_timezone)})",
                    ),
                    parameters=params,
                    work_pool_name=work_pool_name,
                    entrypoint_type=EntrypointType.MODULE_PATH,
                )
            )
        except Exception as exc:
            active_logger.warning("to_deployment failed for %s: %s", toml_path.name, exc)
            continue
    return deployments


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
                LoadError(
                    pack=ep.name, error=f"entry point is not callable: {register_fn!r}"
                )
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
