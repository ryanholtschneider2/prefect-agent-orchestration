# Plan — prefect-orchestration-4h8.4 (`po watch <issue-id>`)

## Affected files

- `prefect_orchestration/watch.py` — **new**. Pure module: event model,
  polling loops, merge queue, renderer. No Typer import (stay testable
  like `status.py`).
- `prefect_orchestration/cli.py` — add `watch` Typer command, wire to
  `watch.run_watch(...)` via `anyio.run`.
- `prefect_orchestration/run_lookup.py` — unchanged; reuse
  `resolve_run_dir`.
- `prefect_orchestration/status.py` — unchanged; reuse
  `find_runs_by_issue_id` (filtered by `issue_id`) to locate the latest
  flow run.
- `tests/test_watch.py` — **new**. Unit tests on the pure helpers.
- `tests/e2e/test_po_watch_cli.py` — **new**. Invoke CLI with a faked
  Prefect client + a real temp run_dir; assert AC behavior.
- `CLAUDE.md` — add `po watch` to the command table under "Debugging a
  run" + the `po` vs `prefect` table.

No deployment/entry-point changes; no formula pack changes.

## Approach

`po watch <issue-id>` resolves the run_dir through `run_lookup`, finds
the most-recent flow run through `status.find_runs_by_issue_id`, and
merges two async producer streams into one ordered consumer that writes
to stdout with `[prefect]` / `[run-dir]` prefixes.

1. **Resolve**. `run_lookup.resolve_run_dir(issue_id)` → `RunLocation`.
   If it raises `RunDirNotFound`, print and exit 2 (matches siblings).
2. **Find flow run**. `find_runs_by_issue_id(client, issue_id=..., limit=10)`
   sorted `EXPECTED_START_TIME_DESC`. Take `runs[0]` if any; else warn
   `no flow run found for issue <id>; watching run_dir only.` and carry
   on — AC4 graceful degradation.
3. **Event model** (`watch.py`):

   ```python
   @dataclass(frozen=True)
   class Event:
       ts: datetime            # aware UTC; tie-break ordering
       source: str             # "prefect" | "run-dir"
       kind: str               # state name / "new" / "modified"
       text: str               # one-line message
   ```

   `render(ev, use_color: bool) -> str` formats one line, e.g.
   `14:32:10 [prefect] Running  → Completed  (flow-run-abc123)` /
   `14:32:11 [run-dir] new     verdicts/build-iter-3.json`. Colors via
   ANSI escapes gated on `sys.stdout.isatty()` (consistent with
   `po artifacts`).

4. **Prefect producer** (`_poll_prefect`): async loop, every 2s call
   `client.read_flow_run(flow_run_id)`, compare `state_name` to the
   prior tick, emit an `Event` on change. Stop when state is in
   `_TERMINAL_STATES` and emit a final `flow terminal: <state>`. Also
   poll task runs (`read_task_runs`, limit 20, sort desc) and emit
   `task <name>: <state>` for new/changed task-run states keyed by
   `task_run.id`. Interval is a constant (e.g. `PREFECT_POLL_S = 2.0`)
   — no noisy sub-second polling.

5. **Run-dir producer** (`_poll_run_dir`): async loop, every 1s walk
   `run_dir` (recursive, follows `verdicts/*.json`, `*.md`, `*.diff`,
   `*.log`). Keep `dict[Path, float]` of seen mtimes; emit `new` on
   first sight, `modified` on mtime bump. Use `asyncio.to_thread` to
   keep the walk off the event loop. Optional `watchdog` short-circuit:
   `try: import watchdog` → use `Observer` when present, fall back to
   polling otherwise (AC5 — no required extra dep).

6. **Merge**. A single `asyncio.Queue[Event]` consumed by the renderer.
   Producers `put` events; consumer `get`s and writes. Merge is
   best-effort chronological by `ev.ts` — with a small 300ms debounce
   window the producer sleeps gives, not a real buffer. Tag prefixes
   are the guard against skew (documented in triage §Ordering).

7. **`--replay`**. Before starting producers, emit synthetic events:
   - all existing files in `run_dir` as `source=run-dir, kind=replay`
     (sorted by mtime), then
   - last N flow-run state transitions: `client.read_flow_run_states(...)`
     (or derive from `flow_run.state_history` if already attached).
     Default N=10, `--replay-n N` overrides.
   After replay, print a `===== live =====` separator and start
   producers.

8. **Ctrl-C**. Wrap producers in `anyio.create_task_group()` (same
   pattern `po status` uses for a single `anyio.run`). On
   `KeyboardInterrupt`, cancel the group; `anyio` swallows the
   sub-exceptions; CLI returns exit 0. No tracebacks on SIGINT.

9. **Graceful degradation** (AC4):
   - Flow run already terminal on startup → skip Prefect producer,
     run run-dir producer only (emit one `flow already <state>` line).
   - Flow run missing entirely → same, with a warning to stderr.
   - `run_dir` disappears mid-run → producer logs `run_dir gone` and
     exits; Prefect producer continues.

10. **No new deps**. `watchdog` is try-imported only; fallback is pure
    stdlib poll. `prefect_client` is already a dep via `status.py`.

## Acceptance criteria (verbatim)

> (1) live stream of both sources in one terminal; (2) Ctrl-C exits
> cleanly; (3) `--replay` prints existing artifacts + last N state
> transitions before following; (4) gracefully degrades if only one
> source is available (e.g., run finished so no more state changes —
> still show new artifacts); (5) no extra deps beyond `prefect_client`
> + watchdog (optional).

## Verification strategy

| AC | How verified |
|----|---|
| 1 | Unit test: drive `merge_events()` with fake prefect + fake run-dir event iterators; assert both prefixes appear in chronological order. E2E: spawn `po watch` in subprocess against a temp rig + stub Prefect client, touch a new file in run_dir while patching client to flip state, read one line containing `[run-dir]` and one containing `[prefect]`. |
| 2 | E2E: SIGINT the subprocess; assert returncode 0 and stderr contains no `Traceback`. |
| 3 | Unit test: `build_replay_events(run_dir, state_history)` returns events sorted by timestamp, with `[run-dir]` entries for each file in run_dir and exactly N `[prefect]` entries. E2E: `po watch <id> --replay --replay-n 3` prints a `===== live =====` separator after the expected number of replay lines. |
| 4 | Unit test: `run_watch` with a terminal-state fake client emits one `flow already <state>` line then only run-dir events. E2E: point at an issue whose flow completed; assert no error exit and run-dir events still stream. |
| 5 | `pyproject.toml` diff review (should be empty). Unit: `watchdog = None` monkeypatch still delivers polling events. |

## Test plan

- **Unit** (`tests/test_watch.py`): `render()`, `merge_events()`,
  `build_replay_events()`, poll-loop state diffing (`_diff_flow_state`,
  `_diff_task_runs`), `run_dir` scanner mtime-diffing, color toggle on
  non-TTY.
- **E2E** (`tests/e2e/test_po_watch_cli.py`): Typer `CliRunner` +
  `monkeypatch` on `prefect.client.orchestration.get_client` returning
  a stub with scriptable `read_flow_run` / `read_task_runs`
  return values. Use `tmp_path` run_dir; write files between "polls"
  and assert lines appear. Cover AC1/3/4; AC2 via subprocess + SIGINT.
- **Playwright**: n/a (CLI only — `has_ui=false`).

## Risks

- **Prefect client internals**: `read_flow_run_states` / state history
  shape can shift between Prefect versions — pin the attribute access
  behind a small helper and fall back to polling snapshots if
  unavailable.
- **Poll interval choice**: 2s flow / 1s files is a guess; tune if the
  Prefect API rate-limits. Constants live at module top for easy bump.
- **Clock skew** between Prefect-server timestamps and local filesystem
  mtimes can reorder events; the prefix tag + human-readable source
  labels are the mitigations — acknowledged in triage.
- **No migrations / no API contract change**: purely additive CLI verb.
  `status.find_runs_by_issue_id` is already reused by tests; no risk
  to existing consumers.
- **Windows**: not supported (Prefect is POSIX; `po logs` already
  execvps `tail`). No new assumption introduced.
