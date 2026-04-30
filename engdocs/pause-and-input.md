# Pause / resume / input

When a flow needs to wait for something a human (or another system)
provides before continuing, Prefect gives three primitives. The shape
you pick should be driven by *who consumes the input*, not by what
feels rigorous.

## Mechanisms at a glance

| | Use it when |
|---|---|
| `pause_flow_run()` (no input) | Flow just sleeps. Resume manually from UI / API / CLI. No value returned. |
| `pause_flow_run(wait_for_input=str)` | One free-form string, rendered as a single text field in the UI. |
| `pause_flow_run(wait_for_input=MyModel)` | Pydantic model auto-rendered as a typed UI form. Returned object has the typed fields. |
| `suspend_flow_run(...)` | Same as pause but releases the worker slot. Use for pauses that may last hours+. |

The same three apply at the task level via `pause_flow_run` from
inside a task — pauses propagate up.

## Schemas are for the consumer, not the producer

The temptation, especially coming from a typed-Python instinct, is to
reach for `RunInput` (Pydantic model) every time you pause. Resist it.

The schema's job is to constrain what the *consumer* of the resumed
value receives. So pick by what comes after the `pause_flow_run` line:

- **Consumer is deterministic code** that switches on the value, calls
  a specific API, or feeds a typed downstream — use `RunInput`. The
  type-safety is load-bearing; the UI form prevents the human from
  typing a value the dispatch can't handle.
- **Consumer is another LLM call** that interprets the human's reply
  as part of a richer reasoning step — use `wait_for_input=str`. The
  LLM *is* the schema; it can read "ship it but mention the staging
  issue" and figure out what that means. A `Literal[...]` field would
  just throw away that nuance.
- **Consumer is just "the human eventually clicks Resume"** with no
  payload needed — bare `pause_flow_run()` with no `wait_for_input`.

The Claude Code analogy is exact: when an agent asks "want me to
A, B, or C?" and the user types "C but skip step 3", no schema is
required because the agent has comprehension. Apply the same logic
to any flow whose resume-step is LLM-backed.

## Concrete patterns

### LLM-backed resume — use a string

```python
@flow
def triage_email(message_id, sender, subject, body):
    classification = _llm_classify(sender, subject, body)
    if classification.confidence < 0.7:
        human_note: str = pause_flow_run(
            wait_for_input=str, timeout=86400,
        )
        decision = _llm_interpret_decision(
            classification=classification, human_note=human_note,
        )
    else:
        decision = classification.decision
    return _create_bead_for(decision, message_id, sender, subject, body)
```

Why `str`: `_llm_interpret_decision` can read freeform text. A typed
model would force you to enumerate decisions you may not yet know
about ("file under project X", "snooze 3 days", "draft a reply
mentioning the SLA").

### Deterministic-code-backed resume — use a model

```python
class TransferApproval(RunInput):
    approved: bool
    reason: str = ""

@flow
def transfer_funds(amount: int, to_account: str):
    if amount > THRESHOLD:
        approval: TransferApproval = pause_flow_run(
            wait_for_input=TransferApproval, timeout=3600,
        )
        if not approval.approved:
            return {"status": "rejected", "reason": approval.reason}
    return _execute_transfer(amount, to_account)
```

Why a model: the next step is `if approval.approved: ...`. Free-form
text would force you to write a parser ("does 'yes please' mean
approved?"). The form's checkbox eliminates the ambiguity at input
time.

### Long pause — use `suspend_flow_run`

```python
@flow
def quarterly_review(report_id: str):
    notes: str = suspend_flow_run(
        wait_for_input=str, timeout=86400 * 30,
    )
    _publish_review(report_id, notes)
```

`pause_flow_run` keeps the worker slot held idle for the entire wait;
`suspend_flow_run` releases it and re-leases on resume. For anything
expected to wait more than minutes, suspend.

## When NOT to use `pause_flow_run` at all

`pause_flow_run` is the right tool when the *exact computation in
flight* must continue from the pause point with the human input
injected. If that's not the case, two cheaper options:

- **`bd human <bead-id>`** — flag a bead for async decision. The
  current flow exits cleanly; whoever picks the bead up next acts on
  the human's response. Right when there's no in-flight cost to
  preserve and you don't care about session continuity.
- **Claude permission prompts / `ExitPlanMode`** — synchronous,
  in-session. Right when the human is *currently sitting at the
  terminal* approving an agent's per-action decisions.

`pause_flow_run` earns its complexity only when:

1. The flow has already done expensive work (LLM calls, scrapes, paid
   API calls) whose result you don't want to recompute.
2. The human responds asynchronously, possibly hours later, possibly
   from a different host.
3. Resumption must continue *the same Python execution* with that
   work-in-progress intact.

If any one of those isn't true, `bd human` or a permission prompt is
simpler and cheaper.

## See also

- [`engdocs/principles.md`](principles.md) — defer to Prefect for
  pure-Prefect concerns.
- An email-handling pack (e.g. `po-formulas-intake`) for a flow that
  *could* gain a pause step at the borderline-confidence branch.
