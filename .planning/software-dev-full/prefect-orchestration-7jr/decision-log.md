# Decision Log — prefect-orchestration-7jr

## Build iter 1 (builder)

- **Decision**: Server-side deployment lookup (Prefect API) instead of
  pack-side `load_deployments()` introspection.
  **Why**: Plan §2 ("we deliberately query the server, not the pack-side
  `register()` output … server-side is the source of truth"). The user
  may have a `register()` that hasn't been `po deploy --apply`'d; the
  AC §4 fallback message specifically tells them to apply, so the
  reachable-server check is the right gate.
  **Alternatives considered**: Pack-side discovery via the existing
  `_deployments.load_deployments()` helper. Rejected — it would let
  `po run --time` succeed even when the server has no such deployment,
  hiding the "you forgot to apply" failure.

- **Decision**: Naive ISO-8601 datetimes are rejected (stricter than
  `status.parse_since`).
  **Why**: Plan §1 — "silently picking UTC for 'schedule at 09:00'
  would surprise users in non-UTC timezones." The `--time` value
  determines *when work runs*, not *which window of past data to
  query*; the cost of a wrong tz is much higher.
  **Alternatives considered**: Match `parse_since`'s forgiving
  "treat naive as UTC" behavior. Rejected for the reason above.

- **Decision**: `--time` is a Typer Option on `cli.run`, not a manual
  pre-pass on `extras`.
  **Why**: Typer cleanly extracts named Options before passing the
  remainder as `ctx.args`, so the existing `_parse_kwargs(extras)` path
  never sees `--time`. This is the same mechanism `--from-file` and
  `--name` already use, so the wiring stays consistent.
  **Alternatives considered**: Scan `extras` for `--time` /
  `--time=value` and pop it. Rejected — duplicates a parser Typer
  already runs and breaks `--time=2h` vs `--time 2h` parity.

- **Decision**: `_run_scheduled()` lifts the scheduled branch out of
  the `run` body into a private helper.
  **Why**: Keeps `run()` readable (one early-return branch, then the
  unchanged sync path). All existing tests for the sync path stay
  green by construction — the helper isn't called when `when is None`.
  **Alternatives considered**: Inline the whole scheduled block.
  Rejected — `run()` was already fairly long; further inlining would
  make the AC §3 "sync unchanged" diff harder to verify by eye.

- **Decision**: `arun_deployment(..., as_subflow=False, timeout=0)`.
  **Why**: Plan §3 — `po run` is invoked outside any Prefect flow
  context, so attaching a parent task-run would error. `timeout=0`
  returns the freshly-`Scheduled` flow-run without polling, which is
  what we want (the worker picks it up at `scheduled_time`, not the
  CLI process).
  **Alternatives considered**: Default `as_subflow=True`, default
  `timeout=None`. Both wrong: the former crashes outside a flow, the
  latter blocks the CLI for the duration of the schedule (e.g. 2h).

- **Decision**: `--time` + `--from-file` is a hard error (exit 2),
  not a "schedule a scratch flow" feature.
  **Why**: Scratch flows aren't entry-point-registered, so there's no
  `<formula>-manual` deployment to schedule against. Supporting that
  would mean dynamically applying a deployment from `.py` source, which
  multiplies the design surface for a workflow PO doesn't need to
  optimize.
  **Alternatives considered**: Silently fall back to sync. Rejected
  — silent fallthrough on flag combinations the user typed is a UX
  smell.

- **Decision**: Mail / file-reservation steps documented in the role
  prompt could not be performed — the `mcp-agent-mail` MCP server is
  not loaded in this session (only Gmail/Calendar MCP tools are
  available, plus a CLI shim that points users back to the MCP).
  **Why**: Tooling availability outside my control.
  **How to apply**: Used scoped `git add <path>` with explicit paths
  for every file the plan listed (no `git add -A` / `git add .`); ran
  `git status --short` post-commit to confirm no other-worker traffic
  in the tree. No collisions encountered.
  **Alternatives considered**: Skip the build step. Rejected — the
  collision-prevention layer is best-effort; scoped staging is the
  durable safeguard in any case.

- **Decision**: `engdocs/principles.md` not edited; §1 already lists
  `po run <formula> --time 2h --args …` as a passing example
  (lines 27–29).
  **Why**: Plan stated "no edit"; the citation belongs in the PR body
  per AC §7. The citation here in the decision log fulfills the
  "design doc" half of AC §7.
  **Alternatives considered**: Add a duplicate cross-reference back
  from §1 to this issue ID. Rejected — principles.md doesn't reference
  bead IDs anywhere else; bead-tracking belongs in `.planning/`, not
  `engdocs/`.
