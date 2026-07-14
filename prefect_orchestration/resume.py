"""`po resume <issue-id>` — relaunch a flow without archiving the run_dir.

Difference vs `po retry`:

- **`po retry`** archives the run_dir to `.bak-<UTC>` and starts fresh
  from triage. Useful when the prior run got into a bad state and you
  want a clean slate.
- **`po resume`** preserves the run_dir as-is. Verdict-bearing tasks
  whose iter bead already carries a `po.<step>` metadata key are
  skipped — the formula's `prompt_for_bead_verdict` short-circuits
  via `PO_RESUME=1` and reads the stamped metadata instead of
  re-prompting the agent. Non-verdict tasks (baseline body, plan body,
  build body) still run; the agent's `--resume <uuid>` keeps it cheap
  because Claude's conversation memory remembers the prior turn.

Use `resume` when a wave wedges deep in the DAG (e.g. on review or
verifier with all upstream verdicts written): it picks up at the
failing step instead of burning 10+ min re-running triage/baseline/plan.

Failure surface (raised as `ResumeError` with a numeric `exit_code`):

- exit 2 — metadata missing / bead unknown
- exit 3 — in-flight run detected, or concurrent resume holds the lock
- exit 4 — formula not installed
- exit 5 — flow raised
- exit 6 — run_dir doesn't exist (nothing to resume — use `po run` instead)
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from prefect_orchestration import run_lookup
from prefect_orchestration.retry import (
    LOCK_SUFFIX,
    _bd_reopen,
    _bd_show_status,
    _exclusive_lock,
    _in_flight_count,
    _load_formula,
    _maybe_clear_stale_lock,
)

DEFAULT_FORMULA = "software-dev-full"


async def _schedule_resume(
    formula_name: str,
    rig_name: str,
    rig_path: Path,
    issue_id: str,
    when: str | None,
) -> tuple[Any, str, Any]:
    """Submit a durable resume flow-run (immediately when ``when`` is None)."""
    from prefect.client.orchestration import get_client

    from prefect_orchestration import scheduling as _scheduling

    if when is None:
        from datetime import datetime, timezone

        scheduled_time = datetime.now(timezone.utc)
    else:
        scheduled_time = _scheduling.parse_when(when)
    runtime_keys = (
        "PO_BACKEND",
        "PO_ACCOUNT",
        "PO_ACCOUNT_CLASS",
        "PO_MODEL_CLI",
        "PO_EFFORT_CLI",
        "PO_START_COMMAND_CLI",
        "PO_CAPACITY_RETRIES",
        "PO_RUNTIME_FALLBACKS",
    )
    runtime_env: dict[str, str] = {}
    manifest_path = (
        rig_path / ".planning" / formula_name / issue_id / ".po-dispatch.json"
    )
    try:
        manifest = json.loads(manifest_path.read_text())
        persisted_env = manifest.get("runtime_env", {})
        if isinstance(persisted_env, dict):
            runtime_env.update(
                {str(key): str(value) for key, value in persisted_env.items()}
            )
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    runtime_env.update(
        {
            key: value
            for key in runtime_keys
            if (value := os.environ.get(key)) is not None
        }
    )
    runtime_env["PO_RESUME"] = "1"
    async with get_client() as client:
        flow_run, full_name, _warn = await _scheduling.submit_scheduled_run(
            client=client,
            formula=formula_name,
            parameters={
                "issue_id": issue_id,
                "rig": rig_name,
                "rig_path": str(rig_path),
            },
            scheduled_time=scheduled_time,
            issue_id=issue_id,
            job_variables={"env": runtime_env},
        )
    return flow_run, full_name, scheduled_time


class ResumeError(RuntimeError):
    """Resume failed; `exit_code` is what the CLI should return."""

    def __init__(self, message: str, *, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class ResumeResult:
    run_dir: Path
    completed_steps: list[str]
    reopened: bool
    flow_result: Any


def _list_completed_steps(run_dir: Path, issue_id: str | None = None) -> list[str]:
    """Return the set of completed step names for the issue.

    Walks iter beads under the seed (`<seed>-<step>-iter<N>`) via
    `bd list --parent <seed>` and returns the stems of every iter bead
    that has metadata under a `po.*` key (excluding bookkeeping keys
    `po.run_dir` / `po.rig_path`).

    If `issue_id` is not supplied (legacy callers), falls back to the
    on-disk `<run_dir>/verdicts/*.json` scan so this function stays
    useful for resuming pre-migration runs.
    """
    if issue_id:
        import json as _json
        import subprocess as _sp

        from prefect_orchestration.beads_meta import iter_bead_re

        proc = _sp.run(
            ["bd", "list", "--parent", issue_id, "--all", "--json"],
            cwd=str(
                run_dir.parent.parent.parent
            )  # rig root: .planning/<formula>/<seed>/
            if run_dir.exists()
            else None,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            try:
                rows = _json.loads(proc.stdout)
            except _json.JSONDecodeError:
                rows = []
            iter_pat = iter_bead_re(issue_id)
            steps: list[str] = []
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                m = iter_pat.match(str(row.get("id", "")))
                if not m:
                    continue
                metadata = row.get("metadata") or {}
                has_verdict = any(
                    str(k).startswith("po.") and k not in {"po.run_dir", "po.rig_path"}
                    for k in metadata
                )
                if has_verdict:
                    steps.append(f"{m.group(1)}-iter-{m.group(2)}")
            return sorted(steps)

    # Legacy fallback for pre-migration run dirs.
    vdir = run_dir / "verdicts"
    if not vdir.is_dir():
        return []
    return sorted(p.stem for p in vdir.glob("*.json"))


def resume_issue(
    issue_id: str,
    *,
    rig: str | None = None,
    force: bool = False,
    formula: str = DEFAULT_FORMULA,
    when: str | None = None,
    foreground: bool = False,
    _in_flight_probe: Callable[[str], int] | None = None,
) -> ResumeResult:
    """Relaunch `formula` on `issue_id` without archiving the run_dir.

    The flow itself sees `PO_RESUME=1` in its environment; the formula's
    verdict-reading helper short-circuits steps whose verdict file
    already exists. `force` bypasses the in-flight Prefect check.
    """
    loc = run_lookup.resolve_run_dir(issue_id)
    rig_path = loc.rig_path
    run_dir = loc.run_dir

    if not run_dir.exists():
        raise ResumeError(
            f"run_dir {run_dir} does not exist — nothing to resume. "
            f"Use `po run {formula} --issue-id {issue_id}` for a fresh run.",
            exit_code=6,
        )

    if not force:
        probe = _in_flight_probe
        if probe is None:

            def probe(iid: str) -> int:
                import anyio

                return anyio.run(_in_flight_count, iid)

        try:
            in_flight = probe(issue_id)
        except Exception as exc:  # noqa: BLE001
            raise ResumeError(
                f"could not check Prefect for in-flight runs: {exc}. "
                "Pass --force to bypass, or run `po status --issue-id "
                f"{issue_id}`.",
                exit_code=3,
            ) from exc
        if in_flight > 0:
            raise ResumeError(
                f"{in_flight} flow run(s) for {issue_id} still Running. "
                f"See `po status --issue-id {issue_id}`, or pass --force.",
                exit_code=3,
            )

    completed = _list_completed_steps(run_dir, issue_id=issue_id)
    lock_path = run_dir.with_name(run_dir.name + LOCK_SUFFIX)
    _maybe_clear_stale_lock(
        lock_path, issue_id, lambda msg: print(msg, file=sys.stderr)
    )

    with _exclusive_lock(lock_path):
        reopened = False
        status_str = _bd_show_status(issue_id)
        if status_str is not None and status_str.lower() != "open":
            _bd_reopen(issue_id)
            reopened = True

        rig_name = rig or rig_path.name

        if not foreground:
            import anyio

            try:
                flow_run, full_name, scheduled_time = anyio.run(
                    _schedule_resume,
                    formula,
                    rig_name,
                    rig_path,
                    issue_id,
                    when,
                )
            except Exception as exc:  # noqa: BLE001
                raise ResumeError(
                    f"failed to schedule resume for {formula!r}: {exc}", exit_code=5
                ) from exc
            return ResumeResult(
                run_dir=run_dir,
                completed_steps=completed,
                reopened=reopened,
                flow_result=(
                    f"submitted flow-run {flow_run.id} ({full_name}) "
                    f"at {scheduled_time.isoformat()}"
                ),
            )

        flow_obj = _load_formula(formula)
        prior_env = os.environ.get("PO_RESUME")
        os.environ["PO_RESUME"] = "1"
        try:
            result = flow_obj(
                issue_id=issue_id,
                rig=rig_name,
                rig_path=str(rig_path),
            )
        except Exception as exc:  # noqa: BLE001
            raise ResumeError(
                f"formula {formula!r} raised: {exc}", exit_code=5
            ) from exc
        finally:
            if prior_env is None:
                os.environ.pop("PO_RESUME", None)
            else:
                os.environ["PO_RESUME"] = prior_env

    return ResumeResult(
        run_dir=run_dir,
        completed_steps=completed,
        reopened=reopened,
        flow_result=result,
    )
