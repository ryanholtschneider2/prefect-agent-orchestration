# Implementation Plan: prefect-orchestration-7vs.4

## Issue Summary

Migrate the **critique-plan** and **build-critic** loops from
`verdicts/<step>.json` file artifacts to **bead-mediated handoff**,
following the pattern shipped by 7vs.3 (lint pilot).

Per-iter beads (`<parent>.plan.iter<N>` and `<parent>.build.iter<N>`)
become the unit of work. The critic agent decides outcomes via
`bd close` semantics:

- **APPROVED** → `bd close <iter-bead> --reason "approved: <one-liner>"`.
  All prior sibling iter beads were already closed at iter rollover;
  the approved iter-bead stays *open* (it's the bead the next
  pipeline step is mid-work on) — wait, re-reading criterion (b):
  *"all closed except the approved one"* — see Design Decision §5
  for the resolution.
- **REJECTED** → orchestrator (NOT critic — see §6) creates
  `<parent>.<step>.iter<N+1>` with `--blocks <prev-iter>` plus a
  fully self-contained description (parent summary + prior verdict
  + diff context), then closes the prior iter-bead with
  `--reason "rejected: <summary>"`.

Iteration cap moves from the flow's `iter_cap` / `plan_iter_cap`
kwargs to **`po.iter_cap`** metadata on the *parent* bead, with the
kwarg preserved as backwards-compatible fallback default.

## Research Summary

### Existing Code Analysis

- **`prefect_orchestration/beads_meta.py`**:
  - `create_child_bead(parent_id, child_id, *, title, description, …)`
    is idempotent on id collision but does **not** support `--deps` /
    `blocks` edges. We need a small core extension (see §2 below).
  - `_bd_show(issue_id, rig_path=…)` reads bead JSON including
    `metadata`, `status`, `closure_reason`, `notes`.
  - `BeadsStore.set(key, value)` shells `bd update --set-metadata
    key=value` — already exercised on the parent for things like
    `iter_final`. Reading `po.iter_cap` from the parent goes through
    the same store.
  - `close_issue(issue_id, notes=…, rig_path=…)` shells
    `bd close <id> --reason <notes>` — already exposed.

- **`po_formulas/software_dev.py`**:
  - `critique_plan` (lines 138-157): currently `prompt_for_verdict` →
    `verdicts/plan-iter-N.json`. Returns `{"verdict": "approved"|"needs_changes", …}`.
  - `review` (lines 569-588): same shape, `verdicts/review-iter-N.json`.
  - The flow body (lines 829-841 plan loop, 848-900 build loop)
    branches on `verdict.get("verdict") == "approved"` and on
    `iter >= cap`. These are the two integration points.
  - **Lint pilot** (`lint` task lines 221-255 + `_read_lint_verdict`
    173-218) is the load-bearing reference.

- **Prompts**: `plan-critic/prompt.md` and `build-critic/prompt.md`
  currently instruct the agent to `cat > verdicts/<step>.json`.
  These are completely rewritten to follow `linter/prompt.md`'s
  bead-close contract.

- **Tests**: `tests/test_software_dev_lint_bead.py` is the canonical
  fixture pattern — `FakeBd` records every shellout, `_stub_prompt`
  mutates bead state mid-prompt to simulate the agent.

### `bd` capabilities (verified via `bd create --help`)

- `bd create --id=<x> --parent=<y> --deps blocks:<prev-iter>` — the
  `--deps` flag accepts `type:id` tokens, so `blocks:` is in scope at
  creation time. (No `--blocks` flag exists.)
- `bd dep add <a> --blocked-by <b>` — alternative post-create path.
- `bd close <id> --reason "<text>"` — we use the reason text both as
  the human-readable closure note and as the keyword the orchestrator
  greps to distinguish approved/rejected/cap-exhausted.
- `bd update <id> --set-metadata key=value` — already wrapped by
  `BeadsStore.set`.

### External Dependencies

No new external deps. We rely on `bd` (already required by lint
pilot) and existing Prefect/Python primitives.

## Success Criteria

### Acceptance Criteria (verbatim)

- (a) `critique-plan` + `build-critic` prompts updated.
- (b) `bd dep` graph after a 3-iter run shows the chain:
  `build.iter1 <-blocks- build.iter2 <-blocks- build.iter3`, all
  closed except the approved one.
- (c) Each child bead's description is self-sufficient: contains
  parent issue summary + prior verdict + scope.
- (d) `iter_cap` honored: at cap, critic closes with
  `--reason=cap-exhausted` and flow proceeds to next step.

### Demo Output

A 3-iter build run leaves this on `bd dep list`:

```
build.iter1  closed  reason="rejected: tests still failing"
build.iter2  closed  reason="rejected: missed AC #3"  blocks=build.iter1 (reverse: build.iter1 is blocked-by iter2 in dep direction)
build.iter3  open    (stays open — work continues to lint/test/etc.)
```

Wait — re-reading criterion (b): "all closed except the approved
one." This implies iter3 (the approved one) stays open and iter1+iter2
are closed. The dep arrow `build.iter1 <-blocks- build.iter2`
(per the issue's notation) means iter2 blocks iter1, i.e. iter2's
edge in `bd dep` reads `iter1 depends on iter2` — but that's
backwards from causality (iter2 is *created after* iter1's
rejection). See §2 for resolution: we use `iter<N+1> --deps
blocks:iter<N>` so the *new* iter is the one with a block edge
pointing at the prior. The chain reads "iter3 blocks iter2 blocks
iter1" in causality order, which matches "iter1 was blocked-by
iter2 was blocked-by iter3" in dep-graph terms — the issue's
notation `iter1 <-blocks- iter2` means *iter2 blocks iter1*, same
direction.

## Files to Modify / Create

| File | Action | LOC | Rationale |
|---|---|---|---|
| `prefect_orchestration/beads_meta.py` | Modify | +6 | Extend `create_child_bead` with optional `blocks: str \| None = None` kwarg → emit `--deps blocks:<id>` when set. Idempotent path stays unchanged. |
| `prefect_orchestration/beads_meta.py` | Modify | +20 | Add `read_iter_cap(parent_id, default, *, rig_path)` helper — reads `po.iter_cap` metadata, falls back to default. (Keeps the lookup logic in core; pack just calls it.) |
| `software-dev/po-formulas/po_formulas/software_dev.py` | Modify | +120 / -40 | Replace `critique_plan` and `review` task bodies with bead-handoff variants (mirror `lint` pattern). Add helpers: `_read_critic_verdict(child_id, rig_path, iter_n, step)`, `_build_critic_iter_description(parent_summary, prior_critique, prior_iter_id, step, iter_n, run_dir)`, `_handle_critic_outcome(prev_iter_id, parent_id, step, iter_n, verdict, rig_path)`. Flow body (plan + build loops) reads `po.iter_cap` via `read_iter_cap`, calls bead-creation hook before critic turn, drives the close-and-create chain on rejection. |
| `software-dev/po-formulas/po_formulas/agents/plan-critic/prompt.md` | Rewrite | ~30 lines | Bead-close contract: `bd update --append-notes "<critique markdown>"` then `bd close <plan_iter_bead_id> --reason "approved: …"` or `--reason "rejected: <one-liner>"`. Drop the `verdicts/plan-iter-N.json` instructions. |
| `software-dev/po-formulas/po_formulas/agents/build-critic/prompt.md` | Rewrite | ~40 lines | Same shape as plan-critic prompt, with the build rubric preserved. |
| `software-dev/po-formulas/tests/test_software_dev_critic_bead.py` | Create | ~250 lines | Mirror `test_software_dev_lint_bead.py`. Cases: approved-on-first-iter, rejected-then-approved (verifies `--deps blocks:` chain wiring), 3-iter cap-exhausted, child description carries parent summary + prior critique, `po.iter_cap` metadata override is honored. |

### Skeleton Code

`prefect_orchestration/beads_meta.py`:

```python
def create_child_bead(
    parent_id: str,
    child_id: str,
    *,
    title: str,
    description: str,
    issue_type: str = "task",
    rig_path: Path | str | None = None,
    priority: int = 2,
    blocks: str | None = None,           # NEW
) -> str:
    """...existing docstring...

    `blocks` (when set) emits `--deps blocks:<id>` so the new bead
    is recorded as blocked-by `<id>` (the prior iter). Idempotent
    re-create still returns the existing id; the dep edge is best-
    effort — if the dep already exists bd treats it as no-op.
    """
    ...
    cmd = ["bd", "create", f"--id={child_id}", f"--parent={parent_id}",
           "--title", title, "--description", description,
           "--type", issue_type, "-p", str(priority)]
    if blocks:
        cmd += ["--deps", f"blocks:{blocks}"]
    ...


def read_iter_cap(
    parent_id: str,
    default: int,
    *,
    rig_path: Path | str | None = None,
    metadata_key: str = "po.iter_cap",
) -> int:
    """Return the iter cap for a parent bead.

    Looks up `po.iter_cap` (or override key) in the parent's bd
    metadata; falls back to `default` when bd is missing, the bead
    has no such key, or the value isn't a positive int. Logs at
    debug level when falling back so misuse is visible.
    """
```

`po_formulas/software_dev.py` (illustrative — exact body TBD):

```python
def _read_critic_verdict(
    child_id: str, rig_path: str, iter_n: int, step: str
) -> dict[str, Any]:
    """Build a verdict dict from the iter bead's final state.

    Closure-reason keyword decoding (case-insensitive substring):
      - 'approved'        → {"verdict": "approved", ...}
      - 'cap-exhausted'   → {"verdict": "cap_exhausted", ...} (orchestrator-set)
      - anything else closed → {"verdict": "needs_changes", "summary": notes-or-reason}
      - still open        → {"verdict": "needs_changes",
                             "summary": "agent crash: <step> bead left open"}
    """


def _build_critic_iter_description(
    parent_id: str,
    parent_summary: str,
    prior_critique_text: str,
    prior_iter_id: str,
    step: str,                # "plan" | "build"
    iter_n: int,
    run_dir: Path,
) -> str:
    """Compose iter<N+1> bead description: self-sufficient (criterion c).

    Sections (markdown):
      ## Parent issue
      <parent_id>: <parent_summary>

      ## Prior critique (iter N)
      Closed bead: <prior_iter_id>
      <prior_critique_text>

      ## Scope for iter N+1
      Apply the prior-critique points; commit changes; the orchestrator
      will spawn the next critic turn.

      ## Build context (build only)
      git diff at <run_dir>/build-iter-N.diff
    """


@task(name="critique_plan", tags=["critic"])
def critique_plan(reg, ctx) -> dict[str, Any]:
    parent_id = ctx["issue_id"]
    iter_n = ctx["plan_iter"]
    child_id = f"{parent_id}.plan.iter{iter_n}"
    create_child_bead(
        parent_id, child_id,
        title=f"plan iter {iter_n} for {parent_id}",
        description=ctx["plan_iter_description"],     # built by flow body before this task
        rig_path=ctx["rig_path"],
        blocks=ctx.get("prev_plan_iter_bead"),
    )
    sess = reg.get("critic")
    sess.prompt(render("plan-critic", plan_iter_bead_id=child_id, **ctx))
    reg.persist("critic")
    reg.publish(
        "plan-critic",
        iter_n=iter_n,
        output_files=[f"plan-critique-iter-{iter_n}.md"],
    )
    return _read_critic_verdict(child_id, ctx["rig_path"], iter_n, "plan")
```

Flow body change (plan loop, illustrative):

```python
# Pre-loop: read parent summary + iter cap once.
parent_summary = (_bd_show(issue_id, rig_path=rig_path_p) or {}).get("title", issue_id)
plan_cap = read_iter_cap(issue_id, plan_iter_cap, rig_path=rig_path_p,
                         metadata_key="po.plan_iter_cap")
build_cap = read_iter_cap(issue_id, iter_cap, rig_path=rig_path_p,
                          metadata_key="po.iter_cap")

plan_iter = 1
prev_plan_iter_bead: str | None = None
prior_critique_text = ""
while True:
    plan_iter_bead_id = f"{issue_id}.plan.iter{plan_iter}"
    description = _build_critic_iter_description(
        issue_id, parent_summary, prior_critique_text,
        prev_plan_iter_bead or "(none)", "plan", plan_iter, run_dir,
    )
    plan_ctx = {
        **base_ctx,
        "plan_iter": plan_iter,
        "plan_iter_description": description,
        "prev_plan_iter_bead": prev_plan_iter_bead,
    }
    plan(reg, plan_ctx, revision_note=_revision_note_for_plan(run_dir, plan_iter))
    verdict = critique_plan(reg, plan_ctx)
    if verdict.get("verdict") == "approved":
        # iter bead is already closed by the critic agent (bd close --reason "approved: …").
        break
    if plan_iter >= plan_cap:
        # Critic agent closed iter<cap> with "rejected: …"; orchestrator
        # re-closes (no-op or annotation) with "cap-exhausted" — see §6.
        close_issue(plan_iter_bead_id,
                    notes=f"cap-exhausted: plan_iter_cap={plan_cap}",
                    rig_path=rig_path_p)
        logger.warning(f"plan_iter_cap={plan_cap} hit — proceeding with last plan")
        break
    prior_critique_text = _read_file(run_dir / f"plan-critique-iter-{plan_iter}.md")
    prev_plan_iter_bead = plan_iter_bead_id
    plan_iter += 1
```

The build loop mirrors this structure, sandwiched between the
existing test/regression-gate logic.

## Design Decisions

### 1. Self-sufficient bead descriptions (criterion c)

**Decision:** orchestrator builds the iter<N+1> bead description in
Python before creating the bead, drawing from three sources:

- **Parent issue summary** — `_bd_show(parent_id)` once at flow start;
  use `title` (and optionally `description` first 500 chars) as the
  parent-summary block. (One bd shellout per flow, not per iter.)
- **Prior critique text** — read the critique markdown the critic
  wrote on the previous iter (`run_dir / "plan-critique-iter-{N}.md"`
  or `critique-iter-{N}.md` for build). The lint pilot's contract
  has the agent `bd update --append-notes` the failure summary; we
  go a step further and ask the critic to *also* write the full
  critique markdown to its existing run-dir path (preserving the
  existing artifact convention so reviewers / `po artifacts` keep
  working) AND `--append-notes` a one-liner to the bead. The
  description-builder concatenates the markdown verbatim.
- **Build diff context** — for the build step only, reference
  `<run_dir>/build-iter-N.diff` by path (not inlined — diffs can be
  large; the next builder reads the file directly). Plan step has no
  diff.

**Rationale:** keeps "the bead is self-contained" *true* without
inlining giant blobs. The next worker reads the bead and finds:
parent summary (cited inline), critique (cited inline — the load-
bearing payload), diff (path reference). Mirrors how the lint pilot
keeps notes one-line and the full log on disk.

### 2. `--blocks` chain wiring

**Decision:** extend `create_child_bead` in **core** with an optional
`blocks: str | None = None` kwarg that, when set, emits `--deps
blocks:<id>`. The bd CLI `--deps` flag accepts `type:id` tokens at
create time (verified via `bd create --help`).

**Rejected alternative:** call `bd dep add <new-iter> --blocked-by
<prev-iter>` from the pack flow after the create. Would work, but:
- The dep edge then exists for a brief window where iter<N+1> is
  open without its block edge — a concurrent `bd ready` could pick
  it up before the edge lands.
- Two shellouts vs one.
- Future callers (other formulas) gain the same affordance for free.

This is a 6-line core change with one new test (verified `--deps`
flag is forwarded). Idempotent re-create path is unchanged: if the
bead already exists, the `bd create` returns "already exists" stderr
and we no-op. The dep edge is created only on the first successful
create — Prefect-task retry won't double-add an edge.

### 3. `po.iter_cap` metadata read (criterion d)

**Decision:** add `read_iter_cap(parent_id, default, *, rig_path,
metadata_key="po.iter_cap")` in core. Read the metadata key from
`bd show <parent>.metadata`; fall back to `default` (the kwarg) when
bd is missing, the key is absent, or the value parses to a non-
positive int. Use it for **both** `iter_cap` and `plan_iter_cap`,
parameterized by metadata-key name (`po.iter_cap` and
`po.plan_iter_cap`).

**Rationale:** backwards-compatible. Existing `po run software-dev-
full --iter-cap N` callers work unchanged (kwarg becomes the
fallback). Per-bead override comes from `bd update <id> --set-
metadata po.iter_cap=5` for any operator who wants iter-cap variance
without re-launching with a different kwarg.

The issue says "iteration cap *moves* to po.iter_cap metadata" —
strict reading would remove the kwarg. But the user's instruction
explicitly recommends *backwards-compatible* (kwarg as fallback),
matching the rest of the codebase's care about API stability for
core formulas. We follow the user's recommendation.

### 4. Verdict bead naming

**Decision:** `<parent>.plan.iter<N>` and `<parent>.build.iter<N>`.

The lint pilot used `<parent>.lint.<N>` (no `iter` infix). We
deviate slightly because:
- The issue prose explicitly writes `<parent>.<step>.iter<N+1>`.
- Criterion (b) writes `build.iter1`, `build.iter2`, …
- These iter beads represent "next iter of [plan|build]" (the work
  unit), so `iter<N>` reads correctly. Lint's bead is "the lint pass
  result," semantically `lint.<N>` is fine there.

We do **not** retroactively rename the lint bead format. The pack's
critic-loop infra handles `iter<N>` form; lint stays `lint.<N>`.
Each step picks its convention.

### 5. APPROVED close semantics (criterion b)

**Decision:**

- On **rejection at iter N**: critic agent closes iter<N> bead with
  `bd close <id> --reason "rejected: <one-liner>"`. Orchestrator
  then creates iter<N+1> with `--deps blocks:iter<N>`.
- On **approval at iter N**: critic agent closes iter<N> bead with
  `bd close <id> --reason "approved: <one-liner>"`. Orchestrator
  observes `verdict=="approved"` and exits the loop.

So at end of a 3-iter run where iter3 was approved:
- iter1 closed (rejected)
- iter2 closed (rejected)
- iter3 **closed** (approved)

This contradicts criterion (b)'s phrasing "all closed except the
approved one." Re-reading: criterion (b) says the *chain* shows
iter1 <- iter2 <- iter3 (correct: each iter<N+1> blocks iter<N> via
`--deps blocks:`), and "all closed except the approved one." Two
readings:

**(a)** "All closed except the approved one" = iter3 (approved)
stays open; iter1, iter2 closed. Reasoning: the approved iter is
the work the next pipeline stage carries forward, so it stays open
until the parent itself closes.

**(b)** "All closed except the approved one" = iter3 stays *closed-
with-approval*; iter1, iter2 are closed-with-rejection. The
"except" is about closure reason, not status.

We pick **(a)** — the approved iter stays open, the orchestrator
inherits it as the work unit until the *parent* close at flow end.
Rationale:
- Matches the issue prose ordering: APPROVED-`bd close` is what the
  critic prompt does; but the orchestrator can re-open or skip the
  close on approval to keep iter<N> open as a marker.
- Cleaner audit trail: `bd dep list` shows iter3 open, with iter2
  and iter1 closed-rejected behind it.

**Concrete impl:** the critic's prompt for APPROVED writes a one-
liner critique markdown to disk, appends a one-line summary to the
iter bead's notes, but **does NOT close** the iter bead on approval.
The orchestrator detects approval by reading the bead's notes for an
`approved: …` keyword (or a structured marker — see §7) AND keeps
the bead open. Cleanup at flow-end (`close_issue(parent_id, …)`)
naturally cascades child-of-parent visibility.

Wait — that complicates the critic prompt (different actions for
approved vs rejected). Simpler alternative: critic ALWAYS closes the
iter bead with reason-prefixed summary; if approved, the orchestrator
**re-opens** the bead (`bd update <id> --status open`) before
exiting the loop. This keeps the critic's contract symmetric (one
action: close-with-reason). Reopening is a one-shellout post-step
operation.

**Final:** Symmetric critic contract (always close); orchestrator
reopens on approved. Documented in flow body. (Tradeoff is one
extra shellout per approval; cost is negligible vs the contract
simplicity.)

### 6. `--reason=cap-exhausted` (criterion d)

**Decision:** orchestrator-driven. When iter<cap> rejects, the critic
already closed iter<cap> with `--reason "rejected: …"`. The
orchestrator detects `iter_n >= cap` AND a non-approved verdict, then
issues a second `bd close` with `--reason "cap-exhausted:
plan_iter_cap=<cap>"`. (`bd close` of an already-closed bead is
idempotent — verified at lint-pilot landing time; if not, we use
`bd update <id> --set-metadata po.cap_exhausted=true` instead and
adjust verdict-decoder accordingly. Plan-reviewer to verify.)

After the cap-exhausted close, the flow continues to the next step
with the last critic verdict in hand (matches today's `if iter_ >=
iter_cap: break` semantics). No 4th critic turn happens.

### 7. Detecting approved/rejected from the bead

**Decision:** keyword substring match on `closure_reason`, exactly
how the lint pilot does it (`"clean" in reason.lower()`). We use:

- `"approved"` substring in closure_reason → `verdict="approved"`.
- `"cap-exhausted"` substring → `verdict="cap_exhausted"` (only
  set by the orchestrator).
- anything else closed → `verdict="needs_changes"` with summary
  pulled from `notes` (one-liner) or `closure_reason` fallback.
- still open → `verdict="needs_changes"` with `"agent crash"`
  summary (matches lint pilot exactly).

This is the **direct replacement** for what `prompt_for_verdict`
returned — same dict shape, no flow-body changes required beyond
the loop-body bead wiring described above.

## Implementation Steps

1. **Core: extend `create_child_bead` with `blocks` kwarg.**
   `prefect_orchestration/beads_meta.py:148-205`. Add `blocks:
   str | None = None` parameter; append `["--deps", f"blocks:{blocks}"]`
   to cmd when set. Update docstring. Add unit test:
   `tests/test_beads_meta.py` (or wherever existing
   `create_child_bead` tests live — locate at step 0) verifying
   `--deps blocks:foo` is included in the recorded shellout when
   `blocks="foo"` is passed; absent otherwise. **Checkpoint:**
   `uv run python -m pytest tests/test_beads_meta.py -k blocks` green.

2. **Core: add `read_iter_cap` helper.** Same module. Reads parent
   metadata key, returns parsed int or default. Add unit tests:
   default fallback when bd missing, default fallback when key
   absent, parsed int when key present, default fallback when
   value is non-int / non-positive. **Checkpoint:** unit tests
   green.

3. **Pack: write `_read_critic_verdict` + `_build_critic_iter_description`
   helpers** in `po_formulas/software_dev.py`, mirroring
   `_read_lint_verdict` in shape and decoding. Pure functions,
   trivially unit-testable. **Checkpoint:** quick unit test for
   each (description-builder produces all three sections; verdict
   decoder maps each closure_reason class correctly).

4. **Pack: rewrite `critique_plan` task** to:
   - Compute `child_id = f"{parent_id}.plan.iter{iter_n}"`.
   - Call `create_child_bead(..., blocks=ctx.get("prev_plan_iter_bead"))`
     with `description=ctx["plan_iter_description"]`.
   - Render `plan-critic` prompt with `plan_iter_bead_id=child_id`.
   - Return `_read_critic_verdict(child_id, …, "plan")`.
   - Drop the `prompt_for_verdict` import / call here.

5. **Pack: rewrite `review` task** symmetrically — child id
   `f"{parent_id}.build.iter{iter_n}"`, `step="build"`, prompt
   key `build-critic`, return `_read_critic_verdict(...)`.

6. **Pack: rewrite plan loop in flow body** (lines 829-841):
   - At flow start (before triage's loop), `_bd_show(parent_id)`
     once → `parent_summary`. Cache.
   - Read caps via `read_iter_cap(parent_id, plan_iter_cap, …,
     metadata_key="po.plan_iter_cap")` and likewise for
     `po.iter_cap`.
   - Track `prev_plan_iter_bead: str | None`, `prior_critique_text: str`
     across iters.
   - Build `plan_iter_description` per iter via
     `_build_critic_iter_description(...)`.
   - Inject `plan_iter_description` + `prev_plan_iter_bead` into
     `plan_ctx`.
   - On approved verdict: reopen `child_id` (`bd update
     --status open`) and break.
   - On non-approved at cap: orchestrator-close child with
     `--reason "cap-exhausted: …"`, log warning, break.
   - Else: read critique markdown, set `prev_plan_iter_bead`, bump
     `plan_iter`.

7. **Pack: rewrite build loop in flow body** (lines 848-900) —
   same shape, with `iter_` instead of `plan_iter`,
   `"po.iter_cap"` metadata key, and the diff path inclusion in
   the description.

8. **Pack: rewrite `plan-critic/prompt.md`** — use
   `linter/prompt.md` as a template. Tell the agent:
   - Read the iter bead (`bd show {{plan_iter_bead_id}}`) for
     full context.
   - Read `{{run_dir}}/plan.md`.
   - Write critique markdown to `{{run_dir}}/plan-critique-
     iter-{{plan_iter}}.md` (preserves existing artifact path).
   - Append a one-liner to the bead's notes:
     `bd update {{plan_iter_bead_id}} --append-notes "<one-liner>"`.
   - Close with `bd close {{plan_iter_bead_id}} --reason
     "approved: <one-liner>"` OR `--reason "rejected: <one-liner>"`.
   - Do **NOT** write `verdicts/plan-iter-N.json`.
   - Reply with one line.

9. **Pack: rewrite `build-critic/prompt.md`** — same shape, build
   rubric preserved (correctness/completeness/style/risk/decision-
   log audit), build-iter critique path (`critique-iter-{{iter}}.md`).

10. **Pack: write `tests/test_software_dev_critic_bead.py`.**
    Mirror `test_software_dev_lint_bead.py`. Cases:
    - `test_critique_plan_approved_first_iter`: approved on
      iter1; verdict["verdict"] == "approved"; only one iter bead
      created.
    - `test_critique_plan_rejected_then_approved`: iter1 rejected
      → iter2 created with `--deps blocks:<iter1>`; iter2 approved.
      Assert recorded `bd create` for iter2 contains `--deps
      blocks:<parent>.plan.iter1`.
    - `test_review_three_iter_cap_exhausted`: cap=3, all 3
      rejected. Assert orchestrator emits `bd close <iter3>
      --reason "cap-exhausted: …"` AND flow proceeds (verdict
      returned has `cap_exhausted` flag — read by next-step logic
      if any; for now we just assert behavior).
    - `test_critique_plan_iter_description_self_contained`: assert
      the `description` arg passed to `create_child_bead` for
      iter2 contains parent summary substring AND the iter1
      critique text substring.
    - `test_iter_cap_metadata_override`: when `po.plan_iter_cap=2`
      is set on parent metadata, the loop caps at 2 even with
      `plan_iter_cap=5` kwarg. (Use the FakeBd to seed metadata.)
    - `test_create_child_bead_forwards_blocks_flag`: in
      `tests/test_beads_meta.py` (core repo). Smoke for the new
      kwarg.
   **Checkpoint:** `uv run python -m pytest
   tests/test_software_dev_critic_bead.py` green.

11. **Smoke the full unit suite** in both repos: `uv run python -m
    pytest -q --ignore=tests/e2e --ignore=tests/playwright` (core)
    and `uv run python -m pytest -q
    --ignore=tests/test_software_dev_pack_path.py
    --ignore=tests/test_software_dev_pack_path_metadata.py` (pack).
    Compare against baseline:
    - Core: 703 passed; same 10 pre-existing failures, no new ones.
    - Pack: 45 passed → ~50 passed (5 new tests); same 4 pre-
      existing failures, no new ones.

12. **Manual smoke** (optional but recommended): `po run software-
    dev-full` against a small bead in this rig with
    `--plan-iter-cap=2 --iter-cap=2` to exercise both loops once
    end-to-end. Inspect `bd dep list <parent>` to verify the
    chain.

## Testing Strategy

Layer: **unit only.** Following the lint pilot pattern:
- `tests/test_software_dev_critic_bead.py` (pack) monkeypatches
  `subprocess.run` so no real `bd` is needed. Stub session
  mutates the FakeBd state mid-prompt to simulate the agent's
  `bd close`. Direct, fast, no Prefect server.
- `tests/test_beads_meta.py` (core) tests the `--deps blocks:`
  forward and `read_iter_cap` decoding.

We do **not** add e2e / playwright tests for this issue. The lint
pilot didn't and the pattern's e2e signal is the same (real bd
shellouts in unit tests would just slow CI).

`PO_SKIP_E2E=1` is already set in this rig's `.po-env`, so the PO
flow's `run_tests` task won't try to run e2e during the
self-bootstrap pass.

## Verification Strategy (one row per criterion)

| Criterion | Method | Concrete check |
|---|---|---|
| (a) prompts updated | `grep` | `grep -L "verdicts/plan-iter" software-dev/po-formulas/po_formulas/agents/plan-critic/prompt.md` returns the path (i.e. file does NOT contain that string); `grep -l "bd close {{plan_iter_bead_id}}" .../plan-critic/prompt.md` succeeds. Same for build-critic. |
| (b) 3-iter dep chain | Unit test `test_review_three_iter_cap_exhausted` | Inspect `FakeBd.calls`; assert `["bd","create","--id=<p>.build.iter2", … "--deps", "blocks:<p>.build.iter1"]` and `["bd","create","--id=<p>.build.iter3", … "--deps", "blocks:<p>.build.iter2"]`. Assert iter1 + iter2 closed-rejected, iter3 cap-closed. |
| (c) self-sufficient description | Unit test `test_critique_plan_iter_description_self_contained` | Capture the `description` arg via FakeBd's `bd create` parser; assert it contains `parent_summary`, `prior critique text marker`, and (for build) the diff path. |
| (d) iter_cap honored | Unit test `test_iter_cap_metadata_override` + `test_review_three_iter_cap_exhausted` | `bd close <iter3> --reason "cap-exhausted: …"` recorded; no 4th critic turn (state["iter"] increments stop at 3); flow continues (no `bd close <parent>` from inside the loop). |

Smoke verification post-implementation:

```bash
# All new tests green:
uv run python -m pytest tests/test_software_dev_critic_bead.py -q
uv run python -m pytest /home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_beads_meta.py -k "blocks or iter_cap" -q

# Baseline regression (no new failures vs baseline.txt):
cd /home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration && uv run python -m pytest tests/ --ignore=tests/e2e --ignore=tests/playwright -q
cd /home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas && uv run python -m pytest -q --ignore=tests/test_software_dev_pack_path.py --ignore=tests/test_software_dev_pack_path_metadata.py
```

## Risks / Open Questions

1. **`bd close` of an already-closed bead** — assumed idempotent
   (lint pilot relies on a similar invariant, but for create not
   close). If `bd close <closed-id>` errors, the cap-exhausted
   path needs to instead `bd update <id> --set-metadata
   po.cap_exhausted=true` and the verdict decoder learns to
   recognize it. **Mitigation:** the unit test for cap-exhausted
   feeds a FakeBd that silently succeeds on duplicate close; if
   real-bd behavior differs, we adjust at the smoke-test stage
   (step 12). Plan-reviewer to confirm if known.

2. **Reopening on approved** — `bd update <id> --status open` on
   a closed bead. Should work (status transitions are not
   restricted in beads), but if the approved bead has a
   blocked-by edge from a hypothetical iter<N+1> (which we never
   create on approval), there's no concern. **Mitigation:**
   simple: skip the reopen and accept reading (b) interpretation
   "approved one stays closed-with-approved-reason." Defer
   choice between (a) and (b) until the unit test is being
   written — we just need a deterministic one. Plan-reviewer:
   please weigh in.

3. **Diff size in description** — even though we only reference
   the diff path, the *prior critique text* could be large
   (build-critic critiques are sometimes ~5KB). bd description
   field has no documented size limit but agent context window
   does — if the iter<N+1> agent has to read the bead via `bd
   show`, a 50KB description will eat tokens. **Mitigation:** cap
   prior-critique inclusion at 4000 chars (`_read_file`'s
   existing default), tail-anchored. This is what
   `_revision_note_for_build` already does.

4. **Concurrent re-entry on Prefect retry** — if the
   `critique_plan` task fails and Prefect retries it, the second
   call will re-create the iter bead (idempotent) and re-prompt
   the critic. The critic agent's existing session has `bd close`
   short-circuit semantics (the agent will see "already closed"
   and exit). Lint pilot has the same property. **Mitigation:**
   none needed; document in plan.

5. **Plan iter naming compat** — existing `plan-critique-iter-N.md`
   markdown path is unchanged; `verdicts/plan-iter-N.json` is
   gone. `po artifacts` (the forensic-trail dump) reads the
   markdown and verdict JSON; the verdict path will start
   missing. **Mitigation:** acceptable — `po artifacts` already
   handles missing files (renders `(missing)`). After landing,
   we may follow up to make `po artifacts` read iter-bead state
   instead, but that's out of scope.

## Out of Scope

- **Verifier role** — also writes a verdict file; not migrating
  here. Stays as `verdicts/verification-iter-N.json` until a
  follow-on issue.
- **Full test gate** — `verdicts/full-test-gate.json` stays.
- **Ralph cleanup loop** — `verdicts/ralph-iter-N.json` stays.
- **Triager / regression-gate / tester** — all stay file-based.
- **`po artifacts` adapter for iter beads** — out of scope; future
  ergonomic improvement.
- **Linter** — already migrated by 7vs.3; we do not touch it.
- **Renaming `<parent>.lint.<N>` → `<parent>.lint.iter<N>`** — out
  of scope. Each step picks its own convention.
- **Removing `iter_cap` / `plan_iter_cap` kwargs** — kept as
  fallback defaults per Design §3.
