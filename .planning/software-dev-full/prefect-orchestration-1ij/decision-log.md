# Decision log — `prefect-orchestration-1ij`

- **Decision**: Hand-rolled fixed-width table rendering instead of `rich.table.Table`.
  **Why**: Keeps output deterministic + testable by substring assertion; `rich` adds ANSI codes and auto-width behavior that complicate unit tests. Critic flagged this as builder's call (nit 3).
  **Alternatives considered**: `rich.table.Table` — rejected for test-friction reasons; can be swapped later if operators want color.

- **Decision**: `check_work_pool_exists` returns FAIL (not skip) when `PREFECT_API_URL` is unset.
  **Why**: A missing API URL means the check physically can't pass; downgrading to a non-FAIL would lie about critical readiness. Message explicitly says "skipped — Prefect API unreachable" with remediation to fix the upstream check.
  **Alternatives considered**: Status.WARN when unreachable — rejected: AC 2 demands critical checks gate exit code, and "no pool visible" is critical.

- **Decision**: Prefect health probe uses `client.hello()` only (no fallback ladder implemented).
  **Why**: Plan §Risks framed the fallback ladder as an "implementation detail" and critic explicitly flagged that the builder should pick one. `hello()` is stable in the pinned Prefect 3.x. Any `Exception` from the probe surfaces as FAIL with the message attached, so API drift degrades gracefully instead of silently passing.
  **Alternatives considered**: try/except cascade `hello → api_healthcheck → httpx.get("/health")` — rejected as premature; add only if a real Prefect upgrade breaks the current call.

- **Decision**: `check_uv_tool_fresh` is WARN on all failure modes (missing `po` binary, timeout, nonzero exit, divergence).
  **Why**: Triage classified stale uv-tool as warn-only; a divergent or missing `po` binary does not impair the current Python process's ability to function.
  **Alternatives considered**: Promoting "divergence" to FAIL — rejected per triage taxonomy.

- **Decision**: CLI wiring uses `@app.command()` with an explicit `raise typer.Exit(report.exit_code)`.
  **Why**: Typer's `CliRunner` captures `Exit` codes correctly and this is the idiomatic pattern already used by `deploy` in the same file. Keeps exit-code logic in one place.
  **Alternatives considered**: `sys.exit` — rejected, breaks `CliRunner` and Typer conventions.

- **Parallel-worker note**: `prefect_orchestration/beads_meta.py` has unstaged edits from another worker (`prefect-orchestration-5i9`), and `cli.py` was extended (added `run_lookup`/`status` imports + `logs` command) by the same. Left both alone beyond the single `_doctor` import addition this issue required.
