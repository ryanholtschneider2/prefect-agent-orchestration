# Bead naming conventions

How PO names the beads it creates inside a `software_dev_full` (graph-mode)
or `epic` run. Useful when reading `bd list`, writing dependency queries,
or wiring new formulas.

## The shape

```
<parent>.<step>[.<iter>]
```

- `<parent>` — the seed bead the user dispatched (e.g.
  `prefect-orchestration-7vs.7`). For an epic, this is the epic id; for
  a single-issue run it's the issue id itself.
- `<step>` — short role/phase token. Stable across runs so per-role
  Claude session UUIDs in `metadata.json` keep resuming the same
  session. Current set: `triage`, `baseline`, `plan`, `plan-critic`,
  `build`, `build-critic`, `lint`, `test`, `regression-gate`,
  `deploy-smoke`, `review-artifacts`, `verify`, `ralph`, `docs`,
  `demo-video`, `learn`.
- `<iter>` — present only on steps that loop under critic rejection
  (`build`, `plan`, `verify`, `ralph`). 1-indexed. The critic creates
  `<parent>.<step>.<N+1>` with `--blocks <parent>.<step>.<N>` when it
  rejects iter N (see `engdocs/formula-modes.md`).

Examples from a real 7vs.5 run:

```
prefect-orchestration-7vs.5            # seed (closed by flow on success)
prefect-orchestration-7vs.5.triage     # one-shot
prefect-orchestration-7vs.5.plan.1     # first plan attempt
prefect-orchestration-7vs.5.plan-critic.1
prefect-orchestration-7vs.5.plan.2     # critic rejected → iter 2
prefect-orchestration-7vs.5.build.1
prefect-orchestration-7vs.5.lint
…
```

## Epic children

Epic fan-out uses dot-suffix children — `<epic>.1`, `<epic>.2`, etc. —
discovered by `epic_run` via either id-probe (`<epic>.1` … `<epic>.N`)
or `bd dep` walk. Once a child is dispatched it gets its own
`<child>.<step>[.<iter>]` sub-graph as above.

## Why dotted names

Three reasons:

1. **Grep-able lineage.** `bd list | grep ^prefect-orchestration-7vs.5`
   shows the entire run's bead trail in topo-ish order.
2. **Stable session affinity.** PO's `metadata.json` keys per-role
   Claude session UUIDs by `<step>` (not by `<step>.<iter>`), so the
   builder's session resumes from iter 1 → iter 2 with full context.
   Renaming a step orphans the session — don't do it casually.
3. **tmux-safe sanitization.** Tmux treats `.` as a pane separator,
   so `4ja.1.build.2` becomes `4ja_1_build_2` in session names. The
   bead id stays dotted; only the tmux session name is sanitized.

## Iter beads as verdict carriers

In graph-mode the critic's verdict IS a new bead, not a JSON file the
orchestrator parses. Pattern:

- Critic reads iter N bead, makes a decision.
- On **approve**: critic closes iter N with a verdict-keyword in
  `--notes` ("APPROVED: …") and the next step's bead becomes ready
  via `bd dep`.
- On **reject**: critic creates iter N+1 with `--blocks <iter-N>` and
  copies its critique into the new bead's description. The next
  builder turn reads ONLY the iter N+1 bead — self-contained, no
  cross-bead context-stitching.

This is why iter beads exist as first-class beads instead of being
collapsed into the parent's notes — the next agent's context is
exactly one `bd show <bead>`.

## Living with the noise

A converged run leaves 10–30 closed beads behind. They're filtered out
of `bd ready` automatically (status=closed). For `bd list` cleanup
preferences, label iter beads at create time and use `bd list --label`
to filter — no PO-side wrapper needed.
