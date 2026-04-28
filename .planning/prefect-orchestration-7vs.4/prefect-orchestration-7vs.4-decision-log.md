# Decision Log: prefect-orchestration-7vs.4

## Decisions

- **Decision**: APPROVED critic outcome closes the iter bead with
  `--reason "approved: <one-liner>"` (interpretation b in the plan
  §5); no reopen.
  **Why**: Symmetric critic contract — agent always calls
  `bd close <iter-bead> --reason "<keyword>: …"`. One fewer shellout
  per approval and the iter bead's status semantics stay consistent
  (every iter ends closed; closure-reason class differentiates).
  Downstream steps work against the *parent* bead, not the iter
  bead, so leaving the iter open buys nothing.
  **Alternatives considered**: Plan §5 also raised "approved iter
  stays open" (interpretation a) so the iter bead acts as a marker
  for the current work unit. Rejected — adds asymmetry to the
  critic prompt and an extra orchestrator shellout (`bd update
  --status open`) for no functional benefit.

- **Decision**: `--deps blocks:<prev>` is added at `bd create` time
  via the new `blocks=` kwarg on `create_child_bead`, not via a
  follow-up `bd dep add`.
  **Why**: One shellout instead of two; eliminates the brief
  window where iter<N+1> exists without its block edge (a
  concurrent `bd ready` could otherwise race).
  **Alternatives considered**: post-create `bd dep add <new>
  --blocked-by <prev>`. Rejected per above.

- **Decision**: `iter_cap` / `plan_iter_cap` kwargs preserved as
  fallback defaults; `po.iter_cap` / `po.plan_iter_cap` parent-bead
  metadata overrides them.
  **Why**: Backwards-compatible — existing `po run software-dev-full
  --iter-cap N` callers keep working, and per-bead variance comes
  from `bd update <id> --set-metadata po.iter_cap=N`. The issue
  prose said cap "moves to" metadata; we read that as "primary
  source becomes metadata," not "remove the kwarg" — matches the
  rest of the codebase's API-stability stance for core formulas.

- **Decision**: Orchestrator (not critic agent) issues the
  `--reason "cap-exhausted: …"` close after iter<cap> is rejected.
  **Why**: Critic agent's contract stays single-action (close with
  approved/rejected). The orchestrator already detects `iter_n >=
  cap` to break the loop; folding the cap-exhausted close into
  that branch keeps cap-policy in the orchestrator. The verdict
  decoder now checks for `"cap-exhausted"` substring in EITHER
  `closure_reason` OR `notes` so an idempotent second close doesn't
  matter — even if `bd close <closed>` is a no-op, the keyword
  reaches the next-iter decode path through whichever field bd
  preserves.
  **Alternatives considered**: have the critic prompt encode the
  cap and self-close with `cap-exhausted`. Rejected — caps are
  policy, prompts are role behavior; mixing them couples the
  critic agent to the iter accounting.

- **Decision**: Per-step iter-bead naming is `<parent>.plan.iter<N>`
  and `<parent>.build.iter<N>` (with `iter` infix), differing from
  the lint pilot's `<parent>.lint.<N>`.
  **Why**: The issue prose explicitly writes `iter<N+1>` and
  acceptance criterion (b) writes `build.iter1`/`build.iter2`. Plan
  and build iter beads represent "next iter of [step]" (work
  unit); the `iter` infix reads correctly there. Lint's bead is
  "the lint pass result" — `lint.<N>` is fine. Each step picks
  its own convention; we don't retroactively rename.

- **Decision**: Keep the per-iter critique markdown artifact
  (`run_dir/plan-critique-iter-N.md`,
  `run_dir/critique-iter-N.md`) — critic agent still writes it —
  in addition to the bead's `--append-notes` one-liner.
  **Why**: `po artifacts <issue>` reads the markdown for the
  forensic-trail dump; removing it would silently break that UX
  (the verdict JSON it also reads is gone, so the markdown is the
  remaining detail). Description-builder reads the same file to
  inline prior critique into iter<N+1>'s self-sufficient
  description, so the file is load-bearing for both archeology
  and handoff.
