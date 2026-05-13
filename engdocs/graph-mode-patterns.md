# Graph-mode / per_role_step patterns (from 7vs.5)

Patterns and gotchas for `software_dev_full` running in graph mode (the
default since 2026-04-29). Read before touching `graph.py`,
`per_role_step`, or any role critic.

## Agent-driven bead closure is the contract; orchestrator is a defensive belt

In graph mode the **agent closes its own role-step bead**; `per_role_step`
only force-closes (with a sentinel `notes="agent did not close role-step bead"`)
when the bead is still open after the @task returns. This mirrors the
7vs.3 lint and 7vs.4 critic patterns. Never make the orchestrator the
primary close path — that erases the verdict-keyword signal the agent
writes.

## Failure paths must not share close semantics with success

`_MAX_PASSES` exhaustion and cap-exhaustion must NOT `bd close` with
`complete` or the same notes as a success. Leave the seed open on
runaway-loop exhaustion so `bd ready` keeps surfacing it for human
triage. Only close iterN beads (and their subtree) with a distinct
`cap-exhausted: …` reason.

## Graph mode: "who creates the iter bead" must be unambiguous

The seed graph creates the role-step iter bead in graph mode; legacy
tasks (lint, critique_plan, review, …) create their own iter bead in
legacy mode. Detect the mode via `ctx.get("role_step_bead_id")` and
reuse the seeded bead — do NOT mint a second one. Two layers both
creating beads with the same shape breaks verdict-reading causality.

## Cross-task state must be reconstructed from disk

Python scope does not exist across Prefect task boundaries. Every
`ctx[...]` value consumed in a legacy task must be reconstructed from
durable artifacts (run_dir files, bead metadata) in graph mode.
Example: `_rebuild_critic_iter_context` re-reads prior critique
markdown + seed-bead title. Audit all `ctx[...]` consumers when
porting a legacy task to graph mode.

## `software_dev_full` must keep explicit kwargs (not `**kwargs`)

`graph.py::_check_formula_signature` validates that named params include
`issue_id`, `rig`, `rig_path`. Pre-existing tests also assert the
public signature. A "thin `**kwargs` dispatcher" plan will fail both
gates. Keep the explicit signature; put the graph-vs-legacy branch in
the body.

## Use `build_registry` for seed bootstrap, not `claim_issue` directly

`build_registry(claim=True)` wraps `claim_issue` with `po-<flow_run_id>`
assignee logic AND creates the run_dir and stamps metadata in one call.
Calling `claim_issue(issue_id, rig_path=...)` bare omits the assignee
param that bd 1.x requires and misses the metadata stamp.

## Phantom-rejection loop: diagnose, don't churn

The dispatcher will sometimes spawn a follow-on iter (`plan.iter2`,
`build.iter3`, `review.iterN+1`) even after the prior iter closed
`approved:`. Symptom: the new iter bead's `{{revision_note}}`
template renders as `(no summary captured)` (or empty), and there's
no actual rejection signal in the prior critic's verdict to act on.
89e hit this three times on one issue (plan iter 2; build iters 2 + 3).

When dispatched on a phantom rejection, the role agent should:

1. **Diagnose** — confirm the prior `<role>.iterN-1` (or
   `<role>-critic.iterN-1`) closed with `approved:` AND that
   `revision_note` is empty / `(no summary captured)`. A real
   rejection without a captured summary is rare but possible; don't
   skip the check.
2. **Don't churn** — DO NOT manufacture cosmetic edits (whitespace,
   ruff-format reflows, no-op refactors, comment tweaks) just to
   produce an artifact. That re-triggers lint+test, burns tokens,
   and corrupts the diff history.
3. **Document** — write a decision-log entry naming the diagnosis
   and the alternatives considered (no-op edit / `bd human` / clean
   close). Re-save the cumulative diff to the iter's expected
   artifact path (`build-iter-N.diff`, etc.) byte-identical to the
   prior iter so downstream verifiers see the right state.
4. **Escalate on recurrence** — if a third consecutive phantom iter
   spawns on the same role, `bd human` to the operator. The
   dispatcher loop is the bug; agents can't fix it from inside the
   loop, and a fourth no-op critique is wasted spend.

The next critic in the chain should approve a phantom-rejection
no-op build with reasoning that explicitly references the prior
approval and the byte-identical diff (89e iter-2 + iter-3 critiques
are the canonical examples). This keeps the loop closeable instead
of inventing fake findings.

**Fast-mode parents are a known phantom source.** When the parent
bead closes with reason `po fast-mode complete`, graph-mode
bookkeeping still seeds `plan` / `plan-critic` (and sometimes
follow-on `build` / `review` iters) even though fast-mode bypassed
planning entirely — there is no `plan.md` on disk and no rejection
signal to act on. `prv` hit this 4 times in one run (plan-critic
iter 1+2, plan iter 2, build iter 3). When you find yourself
dispatched on a fast-mode parent: confirm parent close-reason +
absence of `plan.md`, apply the diagnose/don't-churn/document
protocol, and (planner only) re-save a byte-identical or empty
`plan.md` so downstream critics see consistent state.

**Closed-and-superseded parents are also a phantom source.** Same
shape as the fast-mode case but triggered by a parent that was closed
manually with a supersede note pointing at a follow-up bead (e.g.
"Superseded — work moved to <new-bead>"). Graph-mode bookkeeping
keeps seeding iters across role boundaries — `pd-hdm9` produced six
consecutive phantoms (plan.iter1, plan.iter2, build.iter1,
review.iter1, build.iter2, docs.iter1), each empty / byte-identical
to the prior, while every critic and reviewer closed `approved:` with
explicit "do NOT seed further iters" guidance. When dispatched on
such a parent: read the parent's close-reason / NOTES, confirm the
supersede pointer, write a one-sentence deferral artifact (`plan.md`
linking to the follow-up; empty `build-iter-N.diff`; `no docs needed`
for the doc role), and close `approved:` with reasoning. Do NOT
implement the rejected approach.

**`bd human` does not stop the dispatcher within the same flow run.**
Filing the escalation correctly surfaces it to the operator, but the
running `po` flow keeps seeding iters across subsequent role boundaries
until either (a) the operator cancels the flow run or (b) the
dispatcher's max-passes cap fires. On `pd-hdm9` the build.iter1 agent
filed an escalation and the dispatcher kept going through review.iter1
+ build.iter2 + docs.iter1. Implications for agents: keep
no-op-closing each phantom iter (don't go silent), re-apply the `human`
label to the *current active* iter bead so the escalation surfaces on
the live one (not just the now-closed earlier iter), and don't expect
the escalation alone to short-circuit the loop.

**Escalation syntax in the `-nanocorps` fork (rocks_project rigs).**
The fork's `bd human` verb is **query-only** (`bd human list / respond
/ dismiss / stats` — see `bd human --help`); it does NOT accept
`<id> --question="..."`. To file an escalation, attach the `human`
label and put the question in `--notes`:
```bash
bd update <iter-bead-id> --add-label=human \
  --notes="phantom rejection #3 on this role; dispatcher loop is the bug, please cancel"
```
That's what `bd human list` reads. The legacy `bd human <id>
--question="..."` form documented elsewhere is upstream-only and silently
fails on this fork. Confirmed `bd 1.0.3-nanocorps`, pd-fi6a iter-3
escalation. Re-apply the label to whichever iter bead is currently
active when the dispatcher seeds a follow-on phantom — closed beads
don't show in `bd human list`.
