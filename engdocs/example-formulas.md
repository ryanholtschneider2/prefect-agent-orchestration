# Example formulas

Concrete example formulas pack authors can copy when designing
standing orders or event-driven automations. These are examples, not
core-shipped formulas.

## Naming rule

If an example is role-specific, name the role in the formula. Prefer:

- `builder-heartbeat`
- `tester-heartbeat`
- `planner-heartbeat`

Avoid a generic `agent-heartbeat` unless the formula truly applies to
many roles unchanged.

## Example 1 — `builder-heartbeat`

**Pack:** `po-formulas-retro` or a small role-ops pack

**Kind:** scheduled standing order

**Purpose:** wake a specific role up on a schedule, let it check for
work, make one unit of progress, then exit.

**Behavior:**

1. Read the builder's inbox.
2. Check for ready beads assigned to or intended for the builder.
3. If nothing is actionable, exit cleanly.
4. If work is ready, resume the builder's stable role session.
5. Do one bounded turn.
6. Leave artifacts / bead updates as the normal flow side effects.

**Why this shape:** PO already has stable per-role session affinity, so
the heartbeat should be "wake, inspect, act, exit" rather than a
never-ending daemon loop.

Sketch:

```python
@flow(name="builder-heartbeat")
def builder_heartbeat(rig_path: str) -> dict:
    messages = inbox("builder")
    ready = list_ready_builder_work(rig_path)
    if not messages and not ready:
        return {"status": "idle"}

    bead_id = pick_next_builder_item(messages, ready)
    sess = AgentSession(role="builder", repo_path=rig_path, ...)
    sess.prompt(f"Check your inbox and bead {bead_id}; make progress.")
    return {"status": "worked", "bead_id": bead_id}
```

Typical deployment:

- Cron every 10-30 minutes during working hours
- Manual trigger for debugging

## Example 2 — `triage-inbox`

**Pack:** `po-formulas-intake`

**Kind:** intake / routing flow

**Purpose:** read inbound email or messages, classify them, and route
them into the right downstream workflow.

**Behavior:**

1. Pull recent untriaged messages from Gmail / IMAP / Slack.
2. Classify each message.
3. Route:
   - create a bead
   - draft a reply
   - ignore / dedupe / snooze
   - dispatch another formula
4. For low-confidence cases, pause for human input or flag `bd human`.

Sketch:

```python
@flow(name="triage-inbox")
def triage_inbox(account: str, limit: int = 25) -> dict:
    msgs = fetch_untriaged_messages(account=account, limit=limit)
    routed = []
    for msg in msgs:
        decision = classify_and_route(msg)
        routed.append(apply_route(decision, msg))
    return {"status": "ok", "count": len(routed)}
```

Typical deployment:

- Cron at `0 8 * * *` as `daily-inbox-triage`
- Event-triggered when the mail provider can emit webhooks

## Example 3 — `on-bd-close`

**Pack:** event-oriented utility pack, or colocated with the consumer
pack

**Kind:** event-driven trigger

**Purpose:** react to bead closure and kick off a follow-on action.

**Behavior:**

1. Observe bead close events.
2. Filter by scope / label / parent / formula family.
3. Trigger one follow-on behavior:
   - dispatch the next formula
   - send a notification
   - update a standing-order summary
   - run a retro / lessons aggregation step

Sketch:

```python
@flow(name="on-bd-close")
def on_bd_close(bead_id: str) -> dict:
    bead = bd_show(bead_id)
    if not should_react(bead):
        return {"status": "ignored"}
    dispatch_follow_on(bead)
    return {"status": "triggered", "bead_id": bead_id}
```

Typical trigger:

- Prefect Automation or external watcher emits the event
- Trigger on `bd close` in a chosen scope

## How these fit together

- `builder-heartbeat` is the example of a **role-specific standing
  order**.
- `triage-inbox` is the example of an **inbound intake formula**.
- `on-bd-close` is the example of an **event-driven follow-on**.

Those three cover the common "scheduled role work", "external inbox
intake", and "internal state transition" shapes without inventing a
new primitive for each one.
