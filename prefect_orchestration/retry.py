"""`po retry <issue-id>` — archive a run_dir and relaunch the formula.

Flow:

1. Resolve `(rig_path, run_dir)` from bd metadata via `run_lookup`.
2. Refuse if another flow for the same issue is already Running on the
   Prefect server (unless `--force`).
3. Take an exclusive advisory lock on `<run_dir>.retry.lock`.
4. Stash `run_dir/metadata.json` bytes if `keep_sessions` is set.
   Note: as of prefect-orchestration-7vs.2, role-session UUIDs live on
   the seed bead (BeadsStore) or in `<seed_run_dir>/role-sessions.json`,
   neither of which is touched by archiving the issue's own run_dir —
   so `--keep-sessions` is a no-op for the new path. It still honours
   legacy `metadata.json`-resident sessions, and the migration shim in
   `RoleSessionStore` reads them back even from an archived run_dir.
5. Rename `run_dir` → `<run_dir>.bak-<UTC-timestamp>`.
6. Re-open the bead (and clear the assignee) if it was closed.
7. Restore `metadata.json` into a freshly-created `run_dir`.
8. Invoke the formula callable in-process: `flow(issue_id=..., rig=...,
   rig_path=...)`.

Failure surface (raised as `RetryError` with a numeric `exit_code`):

- exit 2 — metadata missing / bead unknown (`run_lookup.RunDirNotFound`)
- exit 3 — in-flight run detected, or concurrent retry holds the lock
- exit 4 — formula not installed
- exit 5 — flow raised (re-raised as RetryError with original exc chained)
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Callable, Iterator

from prefect_orchestration import run_lookup, status as _status

FORMULA_STAMP = ".po-formula"
LOCK_SUFFIX = ".retry.lock"


class RetryError(RuntimeError):
    """Retry failed; `exit_code` is what the CLI should return."""

    def __init__(self, message: str, *, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class RetryResult:
    archived_to: Path | None
    reopened: bool
    kept_sessions: bool
    launched: bool
    flow_result: Any


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _load_formula(name: str) -> Callable[..., Any]:
    try:
        eps = entry_points(group="po.formulas")
    except TypeError:
        eps = entry_points().get("po.formulas", [])  # type: ignore[assignment]
    for ep in eps:
        if ep.name == name:
            try:
                return ep.load()  # type: ignore[no-any-return]
            except Exception as exc:  # noqa: BLE001
                raise RetryError(
                    f"failed to load formula {name!r}: {exc}", exit_code=4
                ) from exc
    raise RetryError(
        f"no formula named {name!r}. Install the pack that provides it "
        "or run `po list`.",
        exit_code=4,
    )


async def _formula_from_prefect_async(issue_id: str) -> str | None:
    """Query the most-recent Prefect flow run for issue_id; return its
    entry-point name (underscore → hyphen normalised), or None on miss.
    """
    from prefect.client.orchestration import get_client
    from prefect.client.schemas.filters import FlowRunFilter, FlowRunFilterName
    from prefect.client.schemas.sorting import FlowRunSort

    async with get_client() as client:
        runs = await client.read_flow_runs(
            flow_run_filter=FlowRunFilter(name=FlowRunFilterName(any_=[issue_id])),
            sort=FlowRunSort.START_TIME_DESC,
            limit=1,
        )
        if not runs:
            return None
        flow_run = runs[0]
        flow = await client.read_flow(flow_run.flow_id)
        ep_name = flow.name.replace("_", "-")
    try:
        eps = entry_points(group="po.formulas")
    except TypeError:
        eps = entry_points().get("po.formulas", [])  # type: ignore[assignment,attr-defined]
    for ep in eps:
        if ep.name == ep_name:
            return ep_name
    return None  # flow name present but pack not installed


def _formula_from_prefect(issue_id: str) -> str | None:
    """Sync wrapper around _formula_from_prefect_async; returns None on any error."""
    try:
        import anyio

        return anyio.run(_formula_from_prefect_async, issue_id)
    except Exception:  # noqa: BLE001
        return None


def _resolve_formula(
    run_dir: Path,
    issue_id: str,
    explicit: str | None,
    warn: Callable[[str], None],
) -> str:
    """Resolve formula name: explicit flag → stamp file → Prefect history → error."""
    if explicit is not None:
        return explicit
    stamp = run_dir / FORMULA_STAMP
    if stamp.exists():
        name = stamp.read_text().strip()
        if name:
            return name
    prefect_name = _formula_from_prefect(issue_id)
    if prefect_name is not None:
        warn(
            f"po retry: no .po-formula stamp found, using Prefect history:"
            f" {prefect_name!r}"
        )
        return prefect_name
    raise RetryError(
        "po retry: cannot determine original formula. "
        "Pass --formula <name> explicitly. Available formulas: run `po list`",
        exit_code=4,
    )


def _bd_show_status(issue_id: str) -> str | None:
    """Return the bead's current `status` field, or None if unreadable."""
    if shutil.which("bd") is None:
        return None
    proc = subprocess.run(
        ["bd", "show", issue_id, "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if isinstance(data, list):
        data = data[0] if data else {}
    status = data.get("status")
    return str(status) if status is not None else None


def _bd_reopen(issue_id: str) -> None:
    """Reopen a closed bead and clear its assignee so `--claim` works."""
    subprocess.run(
        ["bd", "update", issue_id, "--status", "open", "--assignee", ""],
        capture_output=True,
        text=True,
        check=False,
    )


async def _in_flight_count(issue_id: str) -> int:
    """Count Running flow runs tagged with this issue_id on the Prefect server."""
    from prefect.client.orchestration import get_client

    async with get_client() as client:
        runs = await _status.find_runs_by_issue_id(
            client,
            issue_id=issue_id,
            state="Running",
            limit=50,
        )
    return len(runs)


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    """Non-blocking `fcntl.flock` wrapper. Raises RetryError(3) on contention."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RetryError(
                f"another `po retry` holds {path}; wait for it to finish.",
                exit_code=3,
            ) from exc
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        os.close(fd)


def _archive_run_dir(run_dir: Path) -> Path | None:
    """Rename run_dir → `<run_dir>.bak-<UTC>`. Return the new path, or None if
    the run_dir was already gone.
    """
    if not run_dir.exists():
        return None
    archived = run_dir.with_name(f"{run_dir.name}.bak-{_utc_stamp()}")
    try:
        run_dir.rename(archived)
    except OSError:
        # Fallback for cross-fs renames.
        shutil.move(str(run_dir), str(archived))
    return archived


async def _schedule_retry(
    formula_name: str,
    rig_name: str,
    rig_path: Path,
    issue_id: str,
    when: str,
) -> tuple[Any, str]:
    """Archive+reopen already done; schedule the retry as a Prefect flow-run."""
    from prefect.client.orchestration import get_client

    from prefect_orchestration import scheduling as _scheduling

    scheduled_time = _scheduling.parse_when(when)
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
        )
    return flow_run, full_name, scheduled_time


def retry_issue(
    issue_id: str,
    *,
    keep_sessions: bool = False,
    rig: str | None = None,
    force: bool = False,
    formula: str | None = None,
    when: str | None = None,
    warn: Callable[[str], None] = lambda _m: None,
    _in_flight_probe: Callable[[str], int] | None = None,
) -> RetryResult:
    """Archive the existing run_dir and relaunch `formula` on the same bead.

    `warn` receives human-readable, non-fatal diagnostics (e.g. missing
    `metadata.json` under `--keep-sessions`). `_in_flight_probe` is a
    seam for tests — defaults to the real Prefect query. Pass `when` to
    schedule as a future Prefect flow-run instead of launching in-process.
    """
    loc = run_lookup.resolve_run_dir(issue_id)
    rig_path = loc.rig_path
    run_dir = loc.run_dir

    if not force:
        probe = _in_flight_probe
        if probe is None:

            def probe(iid: str) -> int:
                import anyio

                return anyio.run(_in_flight_count, iid)

        try:
            in_flight = probe(issue_id)
        except Exception as exc:  # noqa: BLE001
            raise RetryError(
                f"could not check Prefect for in-flight runs: {exc}. "
                "Pass --force to bypass, or run `po status --issue-id "
                f"{issue_id}`.",
                exit_code=3,
            ) from exc
        if in_flight > 0:
            raise RetryError(
                f"{in_flight} flow run(s) for {issue_id} still Running. "
                f"See `po status --issue-id {issue_id}`, or pass --force.",
                exit_code=3,
            )

    lock_path = run_dir.with_name(run_dir.name + LOCK_SUFFIX)

    with _exclusive_lock(lock_path):
        # Kill any tmux artifacts left behind by the prior crashed run
        # before relaunching. Without this, a hung flow's tmux session +
        # its claude child + `sleep infinity` keep-alive pane persist
        # indefinitely and stomp the new spawn's session-name collision
        # check (sav.3).
        from prefect_orchestration import tmux_tracker

        try:
            tmux_tracker.kill_for_issue(issue_id)
        except Exception as exc:  # noqa: BLE001
            warn(f"tmux pre-cleanup for {issue_id} failed (non-fatal): {exc}")

        stashed_metadata: bytes | None = None
        if keep_sessions:
            meta_file = run_dir / "metadata.json"
            if meta_file.exists():
                stashed_metadata = meta_file.read_bytes()
            else:
                warn(
                    f"--keep-sessions: no metadata.json under {run_dir}; "
                    "flow will generate fresh session UUIDs."
                )

        archived = _archive_run_dir(run_dir)

        reopened = False
        status_str = _bd_show_status(issue_id)
        if status_str is not None and status_str.lower() != "open":
            _bd_reopen(issue_id)
            reopened = True

        if stashed_metadata is not None:
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "metadata.json").write_bytes(stashed_metadata)

        resolved_formula = _resolve_formula(run_dir, issue_id, formula, warn)
        rig_name = rig or rig_path.name

        if when is not None:
            import anyio

            try:
                flow_run, full_name, scheduled_time = anyio.run(
                    _schedule_retry,
                    resolved_formula,
                    rig_name,
                    rig_path,
                    issue_id,
                    when,
                )
            except Exception as exc:  # noqa: BLE001
                raise RetryError(
                    f"failed to schedule retry for {resolved_formula!r}: {exc}",
                    exit_code=5,
                ) from exc
            return RetryResult(
                archived_to=archived,
                reopened=reopened,
                kept_sessions=stashed_metadata is not None,
                launched=True,
                flow_result=(
                    f"scheduled flow-run {flow_run.id} ({full_name}) "
                    f"at {scheduled_time.isoformat()}"
                ),
            )

        flow_obj = _load_formula(resolved_formula)
        try:
            result = flow_obj(
                issue_id=issue_id,
                rig=rig_name,
                rig_path=str(rig_path),
            )
        except Exception as exc:  # noqa: BLE001
            raise RetryError(
                f"formula {resolved_formula!r} raised: {exc}", exit_code=5
            ) from exc

    return RetryResult(
        archived_to=archived,
        reopened=reopened,
        kept_sessions=stashed_metadata is not None,
        launched=True,
        flow_result=result,
    )
