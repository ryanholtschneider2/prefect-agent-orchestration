<!-- Reusable prompt fragment: inline into builder/critic/verifier role prompts.
     Placeholders use `{{var}}` syntax matching `prefect_orchestration.templates.render_template`.
     Callers substitute `role` before sending the prompt to the agent. -->


### Check your inbox first

Before producing a verdict or starting a new turn, run:

```python
from po_formulas.mail import inbox, mark_read

for msg in inbox("{{role}}"):
    # read msg.subject / msg.body / msg.from_agent
    # address the request, then:
    mark_read(msg.id)
```

- Messages are beads issues labeled `mail` and assigned to you.
- Acknowledge each message by calling `mark_read(msg.id)` once you've
  handled it, so it does not reappear on subsequent turns.
- If you need to reply, call `send(to=msg.from_agent, subject=..., body=..., from_agent="{{role}}")`.
