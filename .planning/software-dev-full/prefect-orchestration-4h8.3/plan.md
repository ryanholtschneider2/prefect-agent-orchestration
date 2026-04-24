# Plan: `po retry <issue-id>` — relaunch with fresh run_dir

Issue: `prefect-orchestration-4h8.3`. Builds on 5i9 (bd metadata for
rig_path + run_dir) and the existing `po status` (for in-flight detection).

## Affected files

- `prefect_orchestration/retry.py` — **new** — core logic: archive
  run_dir, copy-forward `metadata.json` on `--keep-sessions`, reopen
  bead, in-flight check (wrapping `status.find_runs_by_issue_id`),
  orchestration to invoke the formula flow.
- `prefect_orchestration/cli.py` — add `retry` Typer command that wires
  CLI options to `retry.retry_issue(...)`.
- `tests/test_retry.py` — **new** — unit tests with mocked bd + Prefect
  client + formula callable, covering archive, reopen, keep-sessions,
  in-flight refusal, concurrent-retry lock.
- `tests/e2e/test_po_retry_cli.py` — **new** — CLI e2e (`po retry
  --help` + happy path with a fake bd + stubbed formula entry point).

## Approach

**New module `retry.py`.** Keep CLI thin per principles §1–§2 and put
testable logic in a non-Typer module, mirroring `status.py` / `run_lookup.py`.

Flow of `retry_issue(issue_id, *, keep_sessions=False, formula="software-dev-full")`:

1. **Resolve prior run via bd metadata** — call
   `run_lookup.resolve_run_dir(issue_id)`. Reuse the same
   `RunDirNotFound` error surface (exit 2). This gives us `rig_path`
   and `run_dir`, and implicitly verifies the bead exists.
2. **In-flight guard (AC5)** — query Prefect server with
   `status.find_runs_by_issue_id(client, issue_id=issue_id,
   state="Running", limit=50)`. If any non-terminal run exists, refuse
   with exit code 3 and a message pointing at `po status --issue-id
   <id>`. Also accept `--force` to bypass (for stuck-state recovery).
   Use Prefect as source of truth — no pid files (matches triage
   recommendation).
3. **Filesystem lock around archive+launch** — open
   `<run_dir>.retry.lock` with `fcntl.flock(LOCK_EX | LOCK_NB)`. If
   locked, exit 3 with "another retry in progress". Held for the
   duration of archive + launch.
4. **Stash metadata.json for keep-sessions** — if `keep_sessions=True`
   and `run_dir/metadata.json` exists, read its bytes into memory
   *before* archival (triage risk #4).
5. **Archive run_dir (AC1)** — rename
   `run_dir` → `run_dir.bak-YYYYMMDDTHHMMSS` (UTC, `strftime(
   "%Y%m%dT%H%M%S")`). Use `Path.rename` (atomic on same
   filesystem); handle `FileNotFoundError` gracefully (nothing to
   archive — still proceed). Do **not** prune old `.bak-*` dirs (out
   of scope; note for follow-up in NOTES).
6. **Reopen bead if closed (AC2)** — `bd show --json` for `status`
   field; if not `"open"`, run `bd update <id> --status=open` and also
   clear `--assignee=""` so the flow's `--claim` works cleanly
   (triage risk #3). Only touch the bead when it was closed.
7. **Restore metadata.json (AC4)** — if we stashed sessions in step 4,
   `mkdir -p` the new `run_dir` and write `metadata.json` back.
   Otherwise leave `run_dir` missing — the flow creates it.
8. **Launch formula (AC3, synchronous)** — load
   `po.formulas` entry points via `cli._load_formulas()` (refactor to
   expose module-level helper) and call the callable directly:
   `flow_obj(issue_id=issue_id, rig=<derived>, rig_path=str(rig_path))`.
   Per principles §2 (Python-over-subprocess), call the flow object
   in-process — simpler, inherits env, returns Python result. `--rig`
   defaults to the rig directory's basename when the caller omits it
   (matches typical `po run` usage).
9. **Release lock** on success or failure (context manager).

**CLI surface.** Add:

```
po retry <issue-id> [--keep-sessions] [--rig <name>] [--force] \
    [--formula software-dev-full]
```

- `issue_id` positional (matches `po logs`).
- `--keep-sessions` bool flag, default False (fresh run per issue design note).
- `--rig` override; default derived from `rig_path.name`.
- `--force` bypass in-flight check.
- `--formula` escape hatch for future formulas (`epic`, etc.), default
  `software-dev-full` per issue title.

Typer passes these through; no `_parse_kwargs` needed (fixed surface).

## Acceptance criteria (verbatim)

(1) existing run_dir archived with timestamp; (2) issue re-opened if
closed; (3) fresh po run launched synchronously; (4) --keep-sessions
preserves session UUIDs; (5) refuses to run if another po run for this
issue is already in-flight (check via po status / pid file).

## Verification strategy

| AC | How checked |
|---|---|
| 1 | Unit test: pre-create `run_dir/foo.txt`; assert post-call that original path does not exist and exactly one sibling matching `run_dir.bak-*` exists containing `foo.txt`. |
| 2 | Unit test: fake bd returns `status="closed"`; assert `bd update <id> --status=open` was invoked via the monkeypatched `subprocess.run`. Inverse test: `status="open"` → no `bd update` call. |
| 3 | Unit test: monkeypatch a stub `software-dev-full` formula; assert called once with `issue_id=`, `rig=`, `rig_path=` kwargs **after** archive + reopen steps (ordering matters). |
| 4 | Unit test: pre-seed `run_dir/metadata.json` with fake UUIDs; run with `keep_sessions=True`; assert new `run_dir/metadata.json` contains identical bytes. Inverse test: default `keep_sessions=False` → new run_dir is empty / metadata not recreated. |
| 5 | Unit test: monkeypatch `find_runs_by_issue_id` to return one fake Running flow run; assert `retry_issue` raises / exits with the "already in-flight" error and did **not** archive or launch. Second test with empty list → proceeds. Third test: `--force` proceeds even when non-empty. |

Plus an e2e CLI smoke: `po retry --help` lists the flag set, and
`po retry <fake-id>` against a fixture rig with a stub formula entry
point exits 0 and leaves a `.bak-*` sibling.

## Test plan

- **Unit** (`tests/test_retry.py`): all 5 ACs above + concurrent-retry
  lock test (hold the flock from one thread, assert the second call
  exits 3). Mock `run_lookup.resolve_run_dir`, `subprocess.run` (bd),
  `find_runs_by_issue_id` (async), and the formula callable.
- **E2E** (`tests/e2e/test_po_retry_cli.py`): subprocess-invoke `po
  retry --help`; then invoke against a tmpdir rig + fake bd stub on
  PATH + monkeypatched entry-point formula. Verifies Typer wiring.
- **No Playwright** — CLI-only (triage flag `has_ui=false`).

## Risks

- **Concurrent retries (triage risk #6)** — handled via `fcntl.flock`
  on `<run_dir>.retry.lock`. Caveat: on NFS, advisory locks can be
  unreliable. Document as POSIX-local assumption (same as existing
  `po logs` exec-tail).
- **Archive rename across filesystems** — `Path.rename` fails if
  `run_dir` and its parent live on different mounts. In practice
  `.planning/<formula>/<issue>/` always lives on the rig root, so
  single-mount. If we ever hit `OSError`, fall back to `shutil.move`.
- **Bead reopen side effects (triage risk #3)** — `bd update
  --status=open` on an already-open bead is a no-op we want to skip to
  avoid noisy metadata churn. We gate the call on the prior status.
  Clearing assignee is also conditional on the bead being closed.
- **Direct flow call vs subprocess (triage risk #5)** — calling
  `flow_obj(**kwargs)` in-process is synchronous and returns the flow
  result to stdout, matching `po run`'s behavior exactly. Logs stream
  to the same file the existing run pipeline uses. No API / consumer
  contract changes.
- **`--keep-sessions` without prior `metadata.json`** — silent no-op
  after archival (new run_dir stays empty, flow generates fresh
  UUIDs). Emit a `typer.echo(... err=True)` warning so the user isn't
  surprised. Not a failure.
- **No migrations, no schema changes, no API contract changes.**
  Purely additive CLI verb + helper module.

## Out of scope

- Pruning old `.bak-*` directories (triage risk #2). Noted for a
  future `po clean` verb.
- Future-scheduled retries (`--in 2h`) — that's `7jr`.
- Subprocess-based relaunch (explicitly rejected per principles §2).
