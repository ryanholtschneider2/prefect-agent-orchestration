# po-formulas-examples

Reference Python formula pack with three small but runnable examples:

- `builder-heartbeat` — a role-specific standing order
- `triage-inbox` — an intake/routing flow
- `on-bd-close` — a close-event follow-on trigger

The formulas operate on a rig-local `.po-example/` directory so they
can be exercised in a dummy repo without Gmail, Slack, or a live beads
server.

## Install

```bash
po install --editable /path/to/prefect-orchestration/packs/po-formulas-examples
po update
po packs
```

## Dummy rig shape

```text
<rig>/
  .po-example/
    inbox/default/untriaged/*.json
    mail/builder.json
    ready/builder.json
    beads/<bead-id>.json
```

The tests under `tests/test_example_formula_pack.py` show concrete seed
files and expected side effects.
