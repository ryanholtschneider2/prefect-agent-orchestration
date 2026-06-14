"""Auto-ensure a Prefect worker on a work pool (prefect-orchestration-2r6n).

The recurring friction: a deployment run / cron pulse / sheriff dispatch
silently queues in `Scheduled` because nobody remembered to run
`prefect worker start --pool <pool>`. `ensure_pool_worker(pool_name)` is the
on-demand safety net: it probes the Prefect API for an online worker on the
pool and, if none, spawns a **detached** worker process that outlives the
calling command.

Idempotent by construction:

- no-op when a worker is already online on the pool, and
- no-op when a local `prefect worker start --pool <pool>` process is already
  running (covers the window between spawn and the first heartbeat),

so calling it from every dispatch path never stacks workers.

Generic Prefect worker management — lives in po core, not tied to any pack.
The persistent fix (an always-on systemd worker unit) ships in
`prefect_orchestration.serve`; this module is the runtime guard for the cases
the systemd unit isn't installed (or is mid-restart).

Disable globally with `PO_AUTO_WORKER=0` (e.g. CI, or an operator who manages
workers by hand).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: Env var that disables auto-spawning. Default behaviour is enabled.
AUTO_WORKER_ENV = "PO_AUTO_WORKER"

#: Default worker type when auto-creating the pool/worker.
DEFAULT_WORKER_TYPE = "process"

_DISABLED_VALUES = {"0", "false", "no", "off"}


def auto_worker_enabled() -> bool:
    """True unless `PO_AUTO_WORKER` is set to a falsey value (0/false/no/off)."""
    return os.environ.get(AUTO_WORKER_ENV, "1").strip().lower() not in _DISABLED_VALUES


@dataclass
class WorkerEnsureResult:
    """Outcome of an `ensure_pool_worker` call.

    `action` is one of:

    - ``"already-online"`` — a worker was already serving the pool (or a local
      worker process is starting up); nothing spawned.
    - ``"spawned"`` — a new detached worker was started (`pid` is set).
    - ``"disabled"`` — auto-worker is turned off via `PO_AUTO_WORKER`.
    - ``"unreachable"`` — the Prefect API couldn't be queried and no online
      count was supplied, so we declined to spawn.
    - ``"failed"`` — a spawn was attempted but raised (`message` has the cause).
    """

    pool: str
    action: str
    message: str
    pid: int | None = None

    @property
    def spawned(self) -> bool:
        return self.action == "spawned"


def _slug(pool_name: str) -> str:
    """Filesystem/process-name-safe form of a pool name."""
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in pool_name)


def count_online_workers(pool_name: str, *, timeout: float = 5.0) -> int | None:
    """Return the number of online workers on `pool_name`.

    Returns ``0`` when the pool simply doesn't exist yet — that is genuinely
    "no workers" (and a spawned `prefect worker start --type process` will
    create the pool), not an error. Returns ``None`` only when the Prefect API
    can't be reached / queried at all, so the caller can distinguish "zero
    workers" from "couldn't tell" and decline to spawn into the void. Mirrors
    the online-status filter used by `po doctor`.
    """
    import asyncio

    try:
        from prefect.client.orchestration import get_client

        async def _probe() -> list:
            async with get_client() as client:
                return await client.read_workers_for_work_pool(pool_name)

        workers = asyncio.run(asyncio.wait_for(_probe(), timeout=timeout))
    except Exception as exc:  # noqa: BLE001 — unknown pool / unreachable / old API
        if _is_missing_pool_error(exc):
            # Pool not created yet → no workers; let the caller spawn one
            # (`prefect worker start --type process` creates the pool).
            return 0
        return None
    online = [w for w in workers if getattr(w, "status", None) in ("ONLINE", "online")]
    return len(online)


def _is_missing_pool_error(exc: Exception) -> bool:
    """True when `exc` indicates the work pool doesn't exist (HTTP 404 or
    Prefect's ObjectNotFound), as opposed to the server being unreachable."""
    try:
        from prefect.exceptions import ObjectNotFound

        if isinstance(exc, ObjectNotFound):
            return True
    except Exception:  # pragma: no cover — extremely old Prefect
        pass
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status == 404


def local_worker_process_running(pool_name: str) -> bool:
    """True if a local `prefect worker start --pool <pool_name>` process exists.

    Dedups the window between spawning a worker and its first heartbeat (before
    which the API reports zero online workers), so rapid repeated calls don't
    stack workers. Best-effort: returns False if `pgrep` is unavailable.
    """
    pgrep = shutil.which("pgrep")
    if not pgrep:
        return False
    try:
        proc = subprocess.run(
            [pgrep, "-af", "worker start"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    for line in proc.stdout.splitlines():
        tokens = line.split()
        # Look for an exact `--pool <pool_name>` pair anywhere in the cmdline.
        for i, tok in enumerate(tokens):
            if tok in ("--pool", "-p") and i + 1 < len(tokens):
                if tokens[i + 1] == pool_name:
                    return True
            if tok in (f"--pool={pool_name}", f"-p={pool_name}"):
                return True
    return False


def worker_log_path(pool_name: str) -> Path:
    """Where a spawned worker's stdout/stderr is appended."""
    return Path.home() / ".prefect" / f"po-worker-{_slug(pool_name)}.log"


def spawn_detached_worker(
    pool_name: str, *, pool_type: str = DEFAULT_WORKER_TYPE
) -> int:
    """Start a detached `prefect worker` on `pool_name`; return its pid.

    The process is launched in its own session (`start_new_session=True`) so it
    survives the parent command exiting. `--type` auto-creates the pool if it
    doesn't exist yet. Output is appended to `worker_log_path(pool_name)`.

    Raises `FileNotFoundError` if `prefect` is not on PATH.
    """
    prefect_bin = shutil.which("prefect")
    if not prefect_bin:
        raise FileNotFoundError("`prefect` not on PATH; cannot spawn a worker")
    log_path = worker_log_path(pool_name)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "ab")  # noqa: SIM115 — handle owned by the child process
    proc = subprocess.Popen(
        [
            prefect_bin,
            "worker",
            "start",
            "--pool",
            pool_name,
            "--type",
            pool_type,
            "--name",
            f"po-auto-{_slug(pool_name)}",
        ],
        stdout=log_fh,
        stderr=log_fh,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    return proc.pid


def ensure_pool_worker(
    pool_name: str,
    *,
    pool_type: str = DEFAULT_WORKER_TYPE,
    online_count: int | None = None,
    quiet: bool = False,
) -> WorkerEnsureResult:
    """Ensure at least one worker serves `pool_name`, spawning one if needed.

    Idempotent: a no-op when a worker is already online or a local worker
    process is already running for this pool. Spawns a detached worker only
    when neither holds.

    `online_count` lets a caller that has *already* probed the API (e.g. the
    async scheduling path, which holds a live client) pass the count in and
    skip the nested-event-loop probe this function would otherwise run. When
    omitted, the API is probed via `count_online_workers`.

    Honors `PO_AUTO_WORKER=0` (returns ``action="disabled"`` without spawning).
    Never raises — failures are captured in the returned result.
    """
    if not auto_worker_enabled():
        return WorkerEnsureResult(
            pool=pool_name,
            action="disabled",
            message=(
                f"auto-worker disabled ({AUTO_WORKER_ENV}); pool {pool_name!r} "
                f"has no auto-started worker. Run `prefect worker start --pool "
                f"{pool_name}` yourself."
            ),
        )

    if online_count is None:
        online_count = count_online_workers(pool_name)
        if online_count is None:
            return WorkerEnsureResult(
                pool=pool_name,
                action="unreachable",
                message=(
                    f"could not query Prefect API for workers on pool "
                    f"{pool_name!r}; not spawning."
                ),
            )

    if online_count > 0:
        return WorkerEnsureResult(
            pool=pool_name,
            action="already-online",
            message=f"pool {pool_name!r}: {online_count} online worker(s) already.",
        )

    if local_worker_process_running(pool_name):
        return WorkerEnsureResult(
            pool=pool_name,
            action="already-online",
            message=(
                f"pool {pool_name!r}: a local worker process is already "
                f"starting up; not spawning another."
            ),
        )

    try:
        pid = spawn_detached_worker(pool_name, pool_type=pool_type)
    except Exception as exc:  # noqa: BLE001 — surface as a result, never crash dispatch
        return WorkerEnsureResult(
            pool=pool_name,
            action="failed",
            message=(
                f"failed to auto-start a worker on pool {pool_name!r}: {exc}. "
                f"Run `prefect worker start --pool {pool_name}` manually."
            ),
        )

    result = WorkerEnsureResult(
        pool=pool_name,
        action="spawned",
        message=(
            f"auto-started a detached worker on pool {pool_name!r} "
            f"(pid {pid}, logs → {worker_log_path(pool_name)})."
        ),
        pid=pid,
    )
    if not quiet:
        print(result.message, file=sys.stderr)
    return result
