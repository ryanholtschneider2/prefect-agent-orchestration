"""Default `po.formulas` entry-points shipped by core.

Currently exposes one formula:

* **`agent-step`** — single-agent-turn formula for leaf beads. The bead's
  description IS the task spec; `po.agent` metadata names the role to
  dispatch (e.g. `triager`, `summarizer`). The agent identity is
  resolved by walking installed packs' `agents/<role>/prompt.md`.

Use case: a bead in a `graph_run` epic that needs ONE agent turn rather
than a multi-step pipeline. Set:

```bash
bd create --id=<id> --title=… --description=<task spec> \\
    --set-metadata po.formula=agent-step \\
    --set-metadata po.agent=<role-name>
```

Then `po run agent-step --issue-id <id> --rig <r> --rig-path <p>`,
or let `po run epic` / `po run graph` dispatch it via per-bead formula
resolution.

The pack-discovery mechanism for agent dirs walks `importlib.metadata`
entry-points in the `po.agents` group (a new EP group) — packs declare
which roles they ship. See `discover_agent_dir` for resolution rules.
"""

from __future__ import annotations

import datetime as _dt
import re as _re
import shutil
import subprocess
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from prefect import flow, get_run_logger

from prefect_orchestration.agent_session import RateLimitError
from prefect_orchestration.agent_step import agent_step


_RESET_RE = _re.compile(
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>am|pm)\s*\((?P<tz>[A-Za-z_]+/[A-Za-z_]+)\)",
    _re.IGNORECASE,
)


def _compute_retry_time(reset_str: str | None, *, buffer_minutes: int = 2) -> _dt.datetime | None:
    """Parse Claude's rate-limit reset string to a tz-aware UTC datetime.

    `reset_str` looks like ``"10:50am (America/New_York)"``. Returns
    ``None`` when the string is missing / unparseable so callers can
    fall through to "fail loud".
    """
    if not reset_str:
        return None
    m = _RESET_RE.search(reset_str)
    if not m:
        return None
    try:
        tz = ZoneInfo(m.group("tz"))
    except Exception:  # noqa: BLE001
        return None
    hour = int(m.group("hour")) % 12
    if m.group("ampm").lower() == "pm":
        hour += 12
    minute = int(m.group("minute"))
    now_local = _dt.datetime.now(tz)
    candidate = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now_local:
        candidate += _dt.timedelta(days=1)
    candidate += _dt.timedelta(minutes=buffer_minutes)
    return candidate.astimezone(_dt.timezone.utc)


def discover_agent_dir(role: str) -> Path:
    """Resolve `<pack>/agents/<role>/` across installed packs.

    Resolution order:

    1. Each entry in the `po.agents` entry-point group is a callable
       returning a list of `(role_name, agent_dir_path)` tuples. First
       match wins.
    2. Fallback: walk `importlib.metadata` distributions, look for any
       package containing `agents/<role>/prompt.md` on disk.
    3. Raise `LookupError` if no agent dir found.
    """
    try:
        eps = entry_points(group="po.agents")
    except TypeError:  # older importlib
        eps = entry_points().get("po.agents", [])  # type: ignore[assignment]
    for ep in eps:
        try:
            registry_fn = ep.load()
        except Exception:  # noqa: BLE001
            continue
        try:
            for name, path in registry_fn() or []:
                if name == role:
                    return Path(path)
        except Exception:  # noqa: BLE001
            continue

    # Fallback: look at every installed po.formulas pack's repo for
    # an `agents/<role>/` dir. This covers packs that haven't migrated
    # to po.agents EP yet but ship agent dirs at the conventional path.
    try:
        formula_eps = entry_points(group="po.formulas")
    except TypeError:
        formula_eps = entry_points().get("po.formulas", [])  # type: ignore[assignment]
    for ep in formula_eps:
        try:
            module = ep.load().__module__
        except Exception:  # noqa: BLE001
            continue
        try:
            import importlib

            mod = importlib.import_module(module)
            mod_file = getattr(mod, "__file__", None)
            if not mod_file:
                continue
            candidate = Path(mod_file).parent / "agents" / role / "prompt.md"
            if candidate.is_file():
                return candidate.parent
            # Also check sibling `agents/` (common when formulas live at
            # `pack/<formula>.py` and agents at `pack/agents/`).
            candidate2 = Path(mod_file).parent.parent / "agents" / role / "prompt.md"
            if candidate2.is_file():
                return candidate2.parent
        except Exception:  # noqa: BLE001
            continue

    raise LookupError(
        f"agent role {role!r} not found in any installed pack. "
        "Either ship `agents/<role>/prompt.md` in your pack or register "
        "via the `po.agents` entry-point group."
    )


def _read_meta(issue_id: str, key: str, rig_path: str) -> str | None:
    """Read a single metadata key off a bead. None when bd missing or unset."""
    if shutil.which("bd") is None:
        return None
    proc = subprocess.run(
        ["bd", "show", issue_id, "--json"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(rig_path),
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        import json

        data = json.loads(proc.stdout)
        row = data[0] if isinstance(data, list) and data else data
        meta = row.get("metadata") or {}
        val = meta.get(key)
        return str(val) if val is not None else None
    except Exception:  # noqa: BLE001
        return None


@flow(name="agent-step", flow_run_name="{issue_id}", log_prints=True)
def agent_step_flow(
    issue_id: str,
    rig: str,
    rig_path: str,
    agent: str | None = None,
    verdict_keywords: str = "",
    parent_bead: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Single-agent-turn formula. Reads bead description as task spec.

    Resolves the agent role from (in order):
      1. Explicit `agent=` arg
      2. Bead metadata `po.agent`
      3. Raises `ValueError`.

    `verdict_keywords` is a comma-separated list (e.g. "approved,rejected")
    parsed from the bead's close-reason after the agent's turn.

    Returns a dict (serialisable for Prefect) with verdict / summary /
    bead_id / closed_by / from_cache fields.
    """
    logger = get_run_logger()
    role = agent or _read_meta(issue_id, "po.agent", rig_path)
    if not role:
        raise ValueError(
            f"agent-step formula: bead {issue_id} has no agent. Either "
            "pass --agent=<role> or set bd metadata `po.agent=<role>`."
        )
    keywords = tuple(k.strip() for k in verdict_keywords.split(",") if k.strip())
    agent_dir = discover_agent_dir(role)
    logger.info("agent-step: bead=%s role=%s agent_dir=%s", issue_id, role, agent_dir)

    try:
        result = agent_step(
            agent_dir=agent_dir,
            task=None,  # bead description IS the task spec
            seed_id=issue_id,
            rig_path=rig_path,
            verdict_keywords=keywords,
            dry_run=dry_run,
        )
    except RateLimitError as exc:
        # OAuth pool exhausted on a hard rate-limit. Reschedule THIS
        # work as a new flow-run on the agent-step deployment for after
        # Claude's reset time, then fail loudly so this run terminates.
        # The bead stays open; the new run picks it up when quota is
        # back. Without this, every fire during a depleted 5h window
        # marks Failed and operators have to `po resume` each one.
        retry_at = _compute_retry_time(exc.reset_time)
        if retry_at is None:
            logger.error(
                "agent-step: bead=%s rate-limit hit but reset_time=%r could not "
                "be parsed; not rescheduling",
                issue_id,
                exc.reset_time,
            )
            raise
        try:
            import asyncio

            from prefect.client.orchestration import get_client

            from prefect_orchestration import scheduling as _scheduling

            params = {
                "issue_id": issue_id,
                "rig": rig,
                "rig_path": rig_path,
                "agent": agent,
                "verdict_keywords": verdict_keywords,
                "parent_bead": parent_bead,
                "dry_run": dry_run,
            }

            async def _resched() -> tuple[Any, str]:
                async with get_client() as client:
                    fr, full_name, _warn = await _scheduling.submit_scheduled_run(
                        client=client,
                        formula="agent-step",
                        parameters=params,
                        scheduled_time=retry_at,
                        issue_id=issue_id,
                    )
                return fr, full_name

            fr, full_name = asyncio.run(_resched())
            logger.warning(
                "agent-step: bead=%s rate-limit (resets %s); rescheduled as "
                "%s @ %s [new run id %s]",
                issue_id,
                exc.reset_time,
                full_name,
                retry_at.isoformat(),
                fr.id,
            )
            raise RuntimeError(
                f"rate-limit, rescheduled to {retry_at.isoformat()} (new run {fr.id})"
            ) from exc
        except RateLimitError:
            raise
        except Exception as resched_exc:  # noqa: BLE001
            logger.error(
                "agent-step: bead=%s rate-limit hit AND reschedule failed: %s",
                issue_id,
                resched_exc,
            )
            raise exc

    # Convert dataclass to dict for Prefect's serialisation.
    return {
        "bead_id": result.bead_id,
        "verdict": result.verdict,
        "summary": result.summary,
        "from_cache": result.from_cache,
        "closed_by": result.closed_by,
    }


__all__ = ["agent_step_flow", "discover_agent_dir"]
