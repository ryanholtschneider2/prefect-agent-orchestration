# Plan — prefect-orchestration-dmy: `po status`

## Approach

Two deliverables:

1. **Tag flow runs with `issue_id:<id>`** (and for `epic_run`, `epic_id:<id>`) on entry so Prefect-server queries can filter/group by bead. Use Prefect's native `tags` facility (it's filterable via `FlowRunFilter.tags`). In Prefect 3 the clean runtime-mutation path is `prefect.runtime.flow_run.add_tags([...])` — if unavailable, fall back to patching via `prefect.client.orchestration.get_client().update_flow_run(flow_run_id, tags=[...])`. Verify which is current in this Prefect version during build.

2. **New `po status` Typer command** that queries `prefect.client.get_client()` for recent flow runs, filters by tag prefix `issue_id:`, groups latest-per-issue, and prints a table.

Design decisions:

- **Native Prefect `tags`** over custom labels — filterable server-side via `FlowRunFilter(tags=FlowRunFilterTags(all_=["issue_id:..."]))` or (for prefix matching) via server-side `tags.like_` if available; otherwise filter client-side.
- **Factor the lookup helper** into `prefect_orchestration/status.py` (pure, async): `find_runs_by_issue_id(...)`, `group_by_issue(...)`, `parse_since(...)`. This is reusable by `po watch` (`zrk`) per triage guidance.
- **"Current step"** = name of the most recent `TaskRun` that is in a non-terminal state, else the latest task-run name. Query `get_client().read_task_runs(flow_run_filter=FlowRunFilter(id={"any_": [fr.id]}))`, sort by `start_time` desc.
- **One row per issue** — if multiple runs exist for the same issue_id, show latest (by `expected_start_time`); add a trailing `(+N older)` hint column when extras exist.
- **`--since` parser**: accept `Nh` / `Nm` / `Nd` / `Nw` relative and ISO-8601 absolute. Hand-rolled (dateutil is already a Prefect transitive), ~10 lines; default `24h`.
- **Error handling / exit code**: AC says "exits 0 always (observation, not check)". Honour that literally — catch connection errors, print a one-line `error: Prefect server unreachable at <URL>` to stderr, exit 0. Tracebacks suppressed.
- **Filters**: `--all` (drop the `--since` default window), `--since <spec>`, `--issue-id <id>` (exact match), `--state <name>` (case-insensitive Prefect state name).

## Affected files

- `prefect_orchestration/cli.py` — new `status` Typer command; thin adapter over helpers.
- `prefect_orchestration/status.py` — **new** — `find_runs_by_issue_id`, `group_by_issue`, `parse_since`, `current_step_for_run`, table formatting. Async internals; CLI wraps with `anyio.run` or `asyncio.run`.
- `po_formulas/software_dev.py` (software-dev pack) — at start of `software_dev_full`, add `issue_id:<id>` tag to current flow run.
- `po_formulas/epic.py` — at start of `epic_run`, add `epic_id:<id>` tag.
- `tests/test_status.py` — **new** — unit tests against a mocked Prefect client (sync + async fakes) covering grouping, `--since` parsing, empty-result, server-down path.
- (No prompt changes, no migrations.)

## Acceptance criteria (verbatim)

1. Each flow run is labeled with `issue_id:<id>` on entry;
2. `po status` prints one table row per issue with state + timing + current step;
3. exits 0 always (observation, not check);
4. `--since` accepts relative (`1h`) and ISO-8601.

## Verification strategy

- **AC1** — unit test: monkey-patch `prefect.runtime.flow_run.add_tags` to a recorder; invoke `software_dev_full.fn(...)` (or its tagging helper) with `dry_run=True` and assert `("issue_id:<id>",)` was added. Also a live-ish check: when Prefect server is up during e2e, run a `dry_run=True` flow and query client — skip if server not present.
- **AC2** — unit test: feed fake `FlowRun` + `TaskRun` objects into `group_by_issue` / `render_table`, assert one row per issue with state, start, duration, current step columns populated.
- **AC3** — unit test: patch `get_client()` to raise `ConnectError`; invoke `status` via `typer.testing.CliRunner`; assert `result.exit_code == 0` and stderr contains `error:`.
- **AC4** — unit test table: `parse_since("1h")`, `parse_since("30m")`, `parse_since("2d")`, `parse_since("2026-04-01T00:00:00Z")` all return a `datetime` (UTC); bad input raises `typer.BadParameter`.

## Test plan

- **Unit (`tests/test_status.py`)** — primary layer. Mock Prefect client; no server required.
- **E2E** — not added; `po status` requires a running Prefect server + real flow runs, which the repo's existing e2e suite does not stand up. Document as manual smoke in the PR.
- **Playwright** — N/A (CLI only).

## Risks

- **Prefect API drift**: `prefect.runtime.flow_run.add_tags` existence varies by Prefect minor version. Fallback: `get_client().update_flow_run(fr_id, tags=[...])`. Build step must verify against installed version and pick one path; avoid feature-flagging both.
- **Tag semantics**: Prefect tags are strings; `issue_id:<id>` is a convention, not typed. Collisions if any other code tags runs with a string beginning `issue_id:` — negligible in this repo.
- **Client-side filter cost**: if prefix filtering isn't server-side, `po status` pulls recent N runs (cap 200) and filters locally. Acceptable for single-user Prefect; document cap.
- **Breaking consumers**: none — adds a new CLI verb and additive tag. No signature changes to existing flows (tagging happens inside the body).
- **Exit-code-0-on-error** is unusual — surfaced here because the AC specifies it; flagged in review notes.
- **`po watch` (zrk)** downstream: keep `find_runs_by_issue_id` free of Typer imports so it's cleanly reusable.
