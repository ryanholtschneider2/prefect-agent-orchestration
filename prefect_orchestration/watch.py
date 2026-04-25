"""Live merged stream of Prefect flow-run state + run_dir artifacts.

Pure helpers used by `po watch <issue-id>`. Kept free of Typer imports
so it can be unit-tested like `status.py`.

Two producers → one consumer queue:

- `_poll_prefect`: re-reads the flow run + its task runs on a fixed
  interval, diffs against the previous snapshot, and emits an `Event`
  for each state transition (flow or task).
- `_poll_run_dir`: walks the run_dir, tracks mtimes, and emits an
  `Event` for each new or modified file.

Merge is best-effort chronological by `Event.ts`. The `[prefect]` /
`[run-dir]` prefixes are the real disambiguator — producer clocks
(Prefect server vs local mtime) can skew by seconds.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Sequence

# Terminal Prefect state types — mirrors `status._TERMINAL_STATES`.
_TERMINAL_STATES = {"COMPLETED", "FAILED", "CRASHED", "CANCELLED", "CANCELLING"}

# Poll intervals. Constants so operators can bump without grepping.
PREFECT_POLL_S: float = 2.0
RUN_DIR_POLL_S: float = 1.0

# Which file extensions inside run_dir are worth announcing.
_WATCH_SUFFIXES: tuple[str, ...] = (".md", ".json", ".diff", ".log", ".txt")

# ANSI escapes — applied only when the renderer is told `use_color=True`.
_ANSI = {
    "prefect": "\x1b[36m",  # cyan
    "run-dir": "\x1b[33m",  # yellow
    "reset": "\x1b[0m",
    "dim": "\x1b[2m",
}

REPLAY_SEPARATOR = "===== live ====="


# ─── event model ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Event:
    """A single line of the merged feed.

    `ts` is an aware UTC datetime used only for merge ordering.
    `source` is `"prefect"` or `"run-dir"`. `kind` is a short tag
    (`state`, `task`, `new`, `modified`, `replay`, `info`). `text` is
    the human-readable body (without any prefix).
    """

    ts: datetime
    source: str
    kind: str
    text: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─── rendering ───────────────────────────────────────────────────────


def render(ev: Event, *, use_color: bool) -> str:
    """Format a single event as a terminal line."""
    # Normalise timestamp to local time for display.
    ts_local = ev.ts.astimezone()
    hhmmss = ts_local.strftime("%H:%M:%S")
    tag = f"[{ev.source}]"
    if use_color:
        color = _ANSI.get(ev.source, "")
        tag = f"{color}{tag}{_ANSI['reset']}"
        hhmmss = f"{_ANSI['dim']}{hhmmss}{_ANSI['reset']}"
    return f"{hhmmss} {tag} {ev.kind:<8} {ev.text}"


def should_use_color(stream: Any = None) -> bool:
    """Enable ANSI colors only when stdout is a real TTY.

    `NO_COLOR` (https://no-color.org/) disables unconditionally.
    """
    if os.environ.get("NO_COLOR"):
        return False
    stream = stream if stream is not None else _sys_stdout()
    return bool(getattr(stream, "isatty", lambda: False)())


def _sys_stdout() -> Any:
    import sys

    return sys.stdout


# ─── run-dir scanning ────────────────────────────────────────────────


def _is_watched(path: Path) -> bool:
    return path.suffix in _WATCH_SUFFIXES


def scan_run_dir(run_dir: Path) -> dict[Path, float]:
    """Return `{path: mtime}` for every watched file under `run_dir`.

    Missing directory → empty dict (callers treat that as `run_dir gone`
    and stop the scanner).
    """
    out: dict[Path, float] = {}
    if not run_dir.exists():
        return out
    for p in run_dir.rglob("*"):
        if not p.is_file() or not _is_watched(p):
            continue
        try:
            out[p] = p.stat().st_mtime
        except OSError:
            continue
    return out


def diff_run_dir(
    prev: dict[Path, float], current: dict[Path, float], *, run_dir: Path
) -> list[Event]:
    """Emit `new` / `modified` events for files that appeared or changed."""
    events: list[Event] = []
    for path, mtime in current.items():
        prior = prev.get(path)
        if prior is None:
            kind = "new"
        elif mtime > prior:
            kind = "modified"
        else:
            continue
        try:
            rel = path.relative_to(run_dir)
        except ValueError:
            rel = path
        events.append(
            Event(
                ts=datetime.fromtimestamp(mtime, tz=timezone.utc),
                source="run-dir",
                kind=kind,
                text=str(rel),
            )
        )
    events.sort(key=lambda e: e.ts)
    return events


# ─── prefect diffing ─────────────────────────────────────────────────


def _state_name_of(obj: Any) -> str | None:
    # flow_run.state_name (set by recent Prefect) / fallback to .state.name
    name = getattr(obj, "state_name", None)
    if name:
        return str(name)
    state = getattr(obj, "state", None)
    if state is not None:
        nested = getattr(state, "name", None)
        if nested:
            return str(nested)
    return None


def _state_type_of(obj: Any) -> str | None:
    t = getattr(obj, "state_type", None)
    if t is not None:
        return str(getattr(t, "value", t)).upper()
    state = getattr(obj, "state", None)
    if state is not None:
        tt = getattr(state, "type", None)
        if tt is not None:
            return str(getattr(tt, "value", tt)).upper()
    return None


def diff_flow_state(prev: str | None, current: str | None, *, flow_name: str) -> Event | None:
    """Emit an event iff the flow state name changed."""
    if current is None or prev == current:
        return None
    prev_s = prev or "?"
    return Event(
        ts=_now(),
        source="prefect",
        kind="state",
        text=f"{prev_s} → {current}  ({flow_name})",
    )


def diff_task_runs(
    prev: dict[str, str], task_runs: Iterable[Any]
) -> tuple[list[Event], dict[str, str]]:
    """Compare task-run states against the prior snapshot.

    `prev` maps `task_run.id → state_name`. Returns (events, new_snapshot).
    """
    new_snapshot: dict[str, str] = {}
    events: list[Event] = []
    for tr in task_runs:
        tr_id = str(getattr(tr, "id", "") or "")
        if not tr_id:
            continue
        name = str(getattr(tr, "name", "?") or "?")
        state_name = _state_name_of(tr) or "?"
        new_snapshot[tr_id] = state_name
        prior = prev.get(tr_id)
        if prior == state_name:
            continue
        if prior is None:
            text = f"task {name}: {state_name}"
        else:
            text = f"task {name}: {prior} → {state_name}"
        events.append(
            Event(ts=_now(), source="prefect", kind="task", text=text)
        )
    return events, new_snapshot


# ─── replay ──────────────────────────────────────────────────────────


def build_run_dir_replay(run_dir: Path) -> list[Event]:
    """Snapshot the run_dir as `replay` events, ordered by mtime asc."""
    snapshot = scan_run_dir(run_dir)
    events: list[Event] = []
    for path, mtime in snapshot.items():
        try:
            rel = path.relative_to(run_dir)
        except ValueError:
            rel = path
        events.append(
            Event(
                ts=datetime.fromtimestamp(mtime, tz=timezone.utc),
                source="run-dir",
                kind="replay",
                text=str(rel),
            )
        )
    events.sort(key=lambda e: e.ts)
    return events


def build_prefect_replay(state_history: Sequence[Any], n: int) -> list[Event]:
    """Convert the last `n` state-history entries into replay events.

    Each entry should expose `.name` / `.timestamp`. Unknown shapes are
    skipped silently — the replay is best-effort forensic, not a check.
    """
    filtered: list[tuple[datetime, str]] = []
    for entry in state_history:
        name = getattr(entry, "name", None) or getattr(entry, "state_name", None)
        ts = (
            getattr(entry, "timestamp", None)
            or getattr(entry, "created", None)
            or getattr(entry, "start_time", None)
        )
        if not name or ts is None:
            continue
        if getattr(ts, "tzinfo", None) is None:
            ts = ts.replace(tzinfo=timezone.utc)
        filtered.append((ts, str(name)))
    filtered.sort(key=lambda r: r[0])
    tail = filtered[-n:] if n > 0 else filtered
    return [
        Event(ts=ts, source="prefect", kind="replay", text=name)
        for ts, name in tail
    ]


def merge_events(streams: Iterable[Iterable[Event]]) -> list[Event]:
    """Merge pre-collected streams into a single chronological list."""
    combined: list[Event] = []
    for s in streams:
        combined.extend(s)
    combined.sort(key=lambda e: e.ts)
    return combined


# ─── async driver ────────────────────────────────────────────────────


WriteFn = Callable[[str], None]


async def _poll_run_dir(
    run_dir: Path,
    queue: asyncio.Queue[Event | None],
    *,
    interval: float = RUN_DIR_POLL_S,
    initial_snapshot: dict[Path, float] | None = None,
) -> None:
    prev = dict(initial_snapshot) if initial_snapshot is not None else scan_run_dir(run_dir)
    try:
        while True:
            await asyncio.sleep(interval)
            current = await asyncio.to_thread(scan_run_dir, run_dir)
            if not current and not run_dir.exists():
                await queue.put(
                    Event(
                        ts=_now(),
                        source="run-dir",
                        kind="info",
                        text=f"run_dir gone: {run_dir}",
                    )
                )
                return
            for ev in diff_run_dir(prev, current, run_dir=run_dir):
                await queue.put(ev)
            prev = current
    except asyncio.CancelledError:
        raise


async def _poll_prefect(
    client: Any,
    flow_run_id: Any,
    flow_name: str,
    queue: asyncio.Queue[Event | None],
    *,
    interval: float = PREFECT_POLL_S,
) -> None:
    prev_state: str | None = None
    task_state: dict[str, str] = {}
    try:
        while True:
            try:
                fr = await client.read_flow_run(flow_run_id)
            except Exception as exc:  # noqa: BLE001 — observational; recover next tick
                await queue.put(
                    Event(
                        ts=_now(),
                        source="prefect",
                        kind="info",
                        text=f"read_flow_run error: {exc}",
                    )
                )
                await asyncio.sleep(interval)
                continue
            current_name = _state_name_of(fr)
            current_type = _state_type_of(fr)
            ev = diff_flow_state(prev_state, current_name, flow_name=flow_name)
            if ev is not None:
                await queue.put(ev)
            prev_state = current_name

            try:
                task_runs = await _read_task_runs(client, flow_run_id)
            except Exception:
                task_runs = []
            if task_runs:
                events, task_state = diff_task_runs(task_state, task_runs)
                for e in events:
                    await queue.put(e)

            if current_type in _TERMINAL_STATES:
                await queue.put(
                    Event(
                        ts=_now(),
                        source="prefect",
                        kind="info",
                        text=f"flow terminal: {current_name}",
                    )
                )
                return
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        raise


async def _read_task_runs(client: Any, flow_run_id: Any) -> list[Any]:
    """Fetch task runs for a flow, shielded from import-order surprises."""
    try:
        from prefect.client.schemas.filters import FlowRunFilter
        from prefect.client.schemas.sorting import TaskRunSort
    except Exception:
        return []
    runs = await client.read_task_runs(
        flow_run_filter=FlowRunFilter(id={"any_": [flow_run_id]}),
        sort=TaskRunSort.EXPECTED_START_TIME_DESC,
        limit=20,
    )
    return list(runs)


async def run_watch(
    *,
    issue_id: str,
    run_dir: Path,
    client_factory: Callable[[], Awaitable[Any]] | None,
    find_flow_run: Callable[[Any], Awaitable[Any | None]] | None,
    write: WriteFn,
    warn: WriteFn,
    replay: bool = False,
    replay_n: int = 10,
    use_color: bool = False,
    poll_prefect_s: float = PREFECT_POLL_S,
    poll_run_dir_s: float = RUN_DIR_POLL_S,
) -> None:
    """Drive the merged watch loop until Ctrl-C or both producers exit.

    `client_factory` / `find_flow_run` are async callables so tests can
    pass plain lambdas; CLI supplies real `get_client()` + a helper.
    """
    flow_run: Any | None = None
    client: Any | None = None
    terminal_on_start = False

    if client_factory is not None and find_flow_run is not None:
        try:
            client = await client_factory()
            flow_run = await find_flow_run(client)
        except Exception as exc:  # noqa: BLE001 — observational
            warn(f"could not query Prefect server: {exc}")
            flow_run = None

    if flow_run is None:
        warn(f"no flow run found for issue {issue_id}; watching run_dir only.")
    else:
        state_type = _state_type_of(flow_run)
        if state_type in _TERMINAL_STATES:
            terminal_on_start = True
            state_name = _state_name_of(flow_run) or "?"
            write(
                render(
                    Event(
                        ts=_now(),
                        source="prefect",
                        kind="info",
                        text=f"flow already {state_name}",
                    ),
                    use_color=use_color,
                )
            )

    # ── replay ──
    if replay:
        events = build_run_dir_replay(run_dir)
        if flow_run is not None:
            history = getattr(flow_run, "state_history", None) or []
            events.extend(build_prefect_replay(history, replay_n))
        for ev in merge_events([events]):
            write(render(ev, use_color=use_color))
        write(REPLAY_SEPARATOR)

    # ── live producers ──
    queue: asyncio.Queue[Event | None] = asyncio.Queue()
    tasks: list[asyncio.Task[None]] = []

    # Seed the run_dir scanner with its replay snapshot so we don't
    # re-announce every existing file as `new` on the first tick.
    initial = scan_run_dir(run_dir) if run_dir.exists() else {}

    if run_dir.exists() and not terminal_on_start:
        tasks.append(
            asyncio.create_task(
                _poll_run_dir(
                    run_dir, queue, interval=poll_run_dir_s, initial_snapshot=initial
                )
            )
        )
    elif terminal_on_start:
        # Flow is already terminal — replay covered the run_dir snapshot,
        # nothing new will land, and a `while True` poller would hang the
        # process forever. Exit after replay.
        pass
    else:
        warn(f"run_dir missing: {run_dir}")

    if flow_run is not None and not terminal_on_start and client is not None:
        flow_name = str(
            getattr(flow_run, "name", None)
            or getattr(flow_run, "flow_name", "")
            or "flow"
        )
        tasks.append(
            asyncio.create_task(
                _poll_prefect(
                    client,
                    getattr(flow_run, "id", None),
                    flow_name,
                    queue,
                    interval=poll_prefect_s,
                )
            )
        )

    if not tasks:
        warn("nothing to watch (no run_dir and no live flow run).")
        return

    drain_task = asyncio.create_task(_drain(queue, write, use_color=use_color))

    try:
        # As each producer finishes naturally, they return. When all do,
        # we stop the drainer too.
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        raise
    finally:
        await queue.put(None)  # drain sentinel
        try:
            await drain_task
        except asyncio.CancelledError:
            pass


async def _drain(
    queue: asyncio.Queue[Event | None], write: WriteFn, *, use_color: bool
) -> None:
    while True:
        ev = await queue.get()
        if ev is None:
            return
        write(render(ev, use_color=use_color))
