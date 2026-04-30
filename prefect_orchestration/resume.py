"""`po resume <issue-id>` — relaunch a flow without archiving the run_dir.

Difference vs `po retry`:

- **`po retry`** archives the run_dir to `.bak-<UTC>` and starts fresh
  from triage. Useful when the prior run got into a bad state and you
  want a clean slate.
- **`po resume`** preserves the run_dir as-is. Verdict-bearing tasks
  whose `verdicts/<step>.json` already exists are skipped — the
  formula's `prompt_for_verdict` short-circuits via `PO_RESUME=1` and
  reads the existing verdict instead of re-prompting the agent.
  Non-verdict tasks (baseline body, plan body, build body) still run;
  the agent's `--resume <uuid>` keeps it cheap because Claude's
  conversation memory remembers the prior turn.

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

import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from prefect_orchestration import run_lookup
DEFAULT_FORMULA = "software-dev-full"

from prefect_orchestration.retry import (
    LOCK_SUFFIX,
    _bd_reopen,
    _bd_show_status,
    _exclusive_lock,
    _in_flight_count,
    _load_formula,
)


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


def _list_completed_steps(run_dir: Path) -> list[str]:
    """Return the set of verdict step names already on disk.

    Each verdict file lives at `<run_dir>/verdicts/<step>.json`; the
    file's stem is the step name (`triage`, `plan-iter-1`, `review-iter-2`,
    …). Empty list if no verdicts dir or no `.json` children.
    """
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

    completed = _list_completed_steps(run_dir)
    lock_path = run_dir.with_name(run_dir.name + LOCK_SUFFIX)

    with _exclusive_lock(lock_path):
        reopened = False
        status_str = _bd_show_status(issue_id)
        if status_str is not None and status_str.lower() != "open":
            _bd_reopen(issue_id)
            reopened = True

        flow_obj = _load_formula(formula)
        rig_name = rig or rig_path.name
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
