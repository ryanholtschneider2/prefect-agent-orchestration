# Rate limits & wedge timeouts (Anthropic Max + agent_step)

- **`agent_step_flow` auto-reschedules on `RateLimitError`.** When the
  OAuth pool is exhausted on a hard rate-limit, the formula parses
  `exc.reset_time` (e.g. `"10:50am (America/New_York)"`) via
  `_compute_retry_time` and submits a fresh scheduled flow-run on
  `agent-step-manual` for `reset + 2m`, then raises `RuntimeError(
  "rate-limit, rescheduled to ...")` so this run ends Failed-with-message.
  Bd issue stays open + claimed for the new run. No operator intervention.
- **`DEFAULT_AGENT_TIMEOUT_S = 5400` (90 min, was 60).** Bumped after
  observing research-heavy agents (polymer-dev get-data) working
  productively right up to the 60 min wall in JSONL transcripts. Raise
  again if a real workload still hits the wall.
- **StepTimeoutError has TWO patterns** worth distinguishing when triaging:
  (A) **Post-bd-close subprocess wedge** — agent finished work, called
  `bd close`, printed summary, then Claude subprocess sat silent for
  10-20 min until the wall killed it. Look for: bd `status: closed`
  with rich `complete:` close-reason despite Prefect-Failed status.
  Work is already preserved; nothing to re-fire. Root cause unclear
  (possibly final API ack hang or sub-agent that doesn't exit).
  (B) **Real budget shortage** — JSONL shows active tool calls within
  ~30s of the kill. Bd still open, no close-reason. Re-fire needed;
  90 min budget should cover most.
- **Cancelling future-Scheduled flow runs is safe for cadence ops.**
  Bd `--claim` happens INSIDE the flow body, not at scheduling time,
  so a Scheduled-but-not-yet-running flow has no bd assignment to
  release. Cancel via `client.set_flow_run_state(state=State(type=
  CANCELLED, ...))` in batch with no bd side effects. Bd issue stays
  open + unassigned; needs explicit re-schedule (orphan-open beads
  are NOT auto-picked-up by most scheduler scripts).
- **Script-created flow runs use auto-named `r.name`** (e.g.
  `"adorable-rook"`) when the script doesn't pass `name=<id>`. So
  `r.name != issue_id` for those runs — read the id from
  `(r.parameters or {}).get("issue_id")` instead. This bites scripts
  that try to reconcile bd issues against Prefect runs by name.
  Pass `name=issue_id` when creating runs to avoid the trap.
