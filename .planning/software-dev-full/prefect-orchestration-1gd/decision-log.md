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
