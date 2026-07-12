# Epic Operations TUI — Completion Audit

**Date:** 2026-07-12

**Design source:** `docs/plans/2026-07-11-epic-operations-tui-design.md`

**Production entry point:** `po tui`

## Outcome

The new Ink/Bun TUI is the sole production implementation. It presents an
epic-first operations tree, drills into child execution detail, exposes the
approved operator actions through a command palette, and restores the terminal
across every tested lifecycle path.

## Acceptance evidence

| Requirement | Evidence |
|---|---|
| Epic-first workload with expandable children | Normalized-model and interaction tests; wide/compact review frames |
| Epic aggregates and child execution detail | Render assertions for progress, dependencies, blockers, attempts, role timeline, live output, history, and artifacts |
| Discoverable approved actions | Registry completeness and palette interaction tests |
| Concrete destructive/bulk previews and post-action verification | Action preview tests; authoritative Beads/Prefect verification tests |
| Independent source degradation | Adapter stale-snapshot tests and degraded-Prefect review frame |
| Refresh preserves selection and scroll | Reducer navigation tests, including lifecycle-header windowing |
| Responsive at 80x24 and narrow drill-down | Render matrix at 160x48, 100x30, 80x24, 60x24, and 50x16 |
| Color, ASCII, width, and non-TTY behavior | Theme/width render tests; `TERM=dumb` fallback; PTY plain-output smoke |
| Terminal lifecycle correctness | PTY smoke: normal exit, resize, SIGINT, SIGTERM, suspend/resume, exceptions, rejection, and tmux handoff |
| Real dispatch and retry | Ephemeral bead `prefect-orchestration-354n`; Prefect runs `c8b10c31…` then `a25ee521…`; fixture deleted after success |
| Old implementation removed from production | Compiled `tui/tui-next/src/cli.tsx` is installed by `po tui update`; legacy source is absent from the dispatch path |

## Important implementation decisions

- Artifact ownership is exact and declared: a path segment must equal a known
  issue ID. Substring inference is forbidden.
- tmux targets are resolved from discovered sessions/windows and formula role
  conventions, including dedicated, forked, scoped, and agentic layouts.
- Selecting historical work hydrates its Prefect task-run roles even when the
  attempt falls outside the initial active-attempt page.
- Dispatch/retry verification requires a newly observed Prefect attempt. Old
  attempts cannot satisfy a mutation.
- Beads comment verification re-reads the authoritative single issue record.
- Live pane output is separate from tmux inventory health, so selecting a run
  cannot corrupt source status.
- Dispatch and retry propagate the detected Beads backend and retry backend;
  the full provider/account/model/effort runtime tuple remains explicit.

## Verification commands

```bash
cd tui
bun run typecheck
bun test
bun run build

cd ..
uv run ruff check tui/test/pty_smoke.py
uv run ruff format --check tui/test/pty_smoke.py
uv run python tui/test/pty_smoke.py tui/dist/po-tui
bun tui/test/real_stack_actions.ts
```

Review frames are generated under `.planning/tui-completion-audit/` using
`tui/test/capture_review_frames.tsx`. They cover wide, compact, narrow,
below-minimum, command-palette, artifact-choice, and degraded-source states.

The repository-level unit target also passes. The unrelated repository E2E
target currently reports `59 passed, 11 skipped, 1 failed`: the remaining graph
test expects `po run --dry-run` to execute a Prefect flow, while the current CLI
contract intentionally returns a no-write summary. The failure reproduces in
isolation and is outside the TUI production path.

## Deliberate first-release boundary

The first release is keyboard-first. Mouse input is not an acceptance criterion
and Ink 7 does not expose a stable mouse-event API. Navigation and every action
remain reachable through arrows/hjkl, Enter, `:`, `?`, and documented shortcuts.
