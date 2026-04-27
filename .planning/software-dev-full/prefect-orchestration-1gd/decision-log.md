# Decision log — prefect-orchestration-1gd

## Build iter 1

- **Decision**: Skipped `mcp-agent-mail` file-reservation handshake.
  **Why**: The `mcp-agent-mail` MCP server is not connected in this session
  (no `mcp__mcp-agent-mail__*` tools are deferred-available; ToolSearch
  for "agent mail reservation" returned no matches; the CLI binary
  exists at `/home/ryan-24/.local/bin/mcp-agent-mail` but explicitly
  refuses CLI invocation and points back at the missing MCP tools).
  Build proceeds with scoped `git add <path>` discipline as the only
  collision-avoidance mechanism.
  **Alternatives considered**: Failing the step with "blocked: mail
  unavailable" — rejected because the only file in scope
  (`tests/test_cli_run_from_file.py`) is unique to this issue and
  `git status` shows it untouched by other workers.

- **Decision**: Imported `prefect_test_harness` at module top, not
  lazily inside the fixture body.
  **Why**: `prefect` is already a hard dep of this project and is
  imported by every other Prefect-using test in the file (via the
  scratch flow source strings, plus indirect imports via
  `prefect_orchestration`). A lazy import buys nothing and just adds
  noise.
  **Alternatives considered**: Lazy import inside the fixture (as
  drafted in the plan) — rejected since `prefect.testing.utilities`
  is part of the `prefect` package itself, no extras gating, no
  optional dep.

- **Decision**: Used `Iterator[None]` from `collections.abc` for the
  fixture return type (matches the suite's existing convention).
  **Why**: Plan suggested either `Iterator[None]` or
  `Generator[None, None, None]`; `Iterator[None]` is simpler and the
  plan flagged it as "already common in the suite" — confirmed via
  grep (other fixtures in the test tree use the same pattern).
  **Alternatives considered**: `Generator[None, None, None]` —
  rejected as more verbose with no benefit for a fixture that yields
  once.

- **Decision**: Soft-reset the first commit and recommitted with an
  explicit pathspec.
  **Why**: First `git commit` ran with no path filter and the index
  contained two sibling-worker plan.md files
  (`prefect-orchestration-7jr/plan.md`,
  `prefect-orchestration-uc0/plan.md`) that had been staged by other
  PO planner steps before mine started. They got swept into my
  commit, which would have falsely attributed their work to issue
  `1gd` and could have broken those workers' subsequent commit flow.
  `git reset --soft HEAD^` (non-destructive — keeps changes in
  index/working tree, repo is local-only with no remote per
  CLAUDE.md) followed by `git restore --staged` of the two sibling
  files unwound the mistake; recommitted only my files. Re-staging
  via `git restore --staged` is the documented "leave the other
  worker's work alone" path from the parallel-run-hygiene rules.
  **Alternatives considered**: `git reset --hard` — explicitly
  forbidden by role rules and would have lost local working-tree
  state. Leaving the bad commit and amending — `git commit --amend`
  is also disallowed by the harness's git-safety protocol; soft
  reset + new commit is the right replacement.
