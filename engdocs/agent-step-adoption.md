# agent_step adoption patterns (non-bead-driven flows)

Lessons from migrating `po-formulas-retro` (a *scheduled* cron flow,
no triggering bead) onto `agent_step`. Apply when porting any flow
whose runs aren't initiated by `po run --issue-id <id>`.

## Mint a real seed bead per run before calling `agent_step`

`agent_step` calls `create_child_bead(seed_id, "<seed>.<step>.iter1", …)`
which requires the parent to be a real bd bead (`bd create` rejects
`--deps parent-child:<missing-id>`). For scheduled flows: synthesize
a `<formula-prefix>-<slug>-<utc-timestamp>` id, `bd create` it at low
priority (`-p 4`), close it at flow end with a one-line summary.
Treat `"already exists"` stderr as success (idempotent on retry).

## Fall back to bd auto-id on prefix mismatch

Some bd configs enforce a fixed auto-id prefix per rig and reject
custom `--id=…`. The seed-bead helper should detect `"prefix mismatch"`
on stderr, retry without `--id`, and parse the assigned id from
`bd create` stdout (`Created <id>: <title>`). Caller captures the
return value — don't assume the requested id was honored.

## `dry_run=True` must short-circuit BEFORE `agent_step`

Under `dry_run=True`, `agent_step` selects `StubBackend`, which by
contract does NOT write the verdict file → `read_verdict()` raises
`FileNotFoundError`. Branch on `dry_run` at the top of the task and
return an empty payload before constructing the agent_step call.
Bonus: dry-runs become free (no token spend, no agent turn).

## Two run dirs is fine when the flow has a documented operator artifact

`agent_step` hardcodes its run_dir to `<rig>/.planning/agent-step/<seed_id>/`
(verdicts + per-role session UUIDs). If the flow's pre-existing
operator-readable summary lives elsewhere (`<rig>/.planning/<formula>/<ts>/`),
keep both — consolidating breaks operator muscle memory and any
README example referencing the summary path. Document the split in
the flow.

## Tests: discover the verdict path from the *stamped bead description*, not the rendered prompt

`agent_step` ships the rendered `task.md` to bd via
`bd update <iter_bead_id> --description …`; the verdict path lives
in `task.md` (e.g. `{{verdict_path}}`), not in the agent's identity
`prompt.md`. The test fake's `subprocess.run` patch should intercept
the `["bd", "update", <id>, "--description", …]` shellout, capture
the description string, and regex-extract the verdict path from
**that**. Patching `agent_step._build_session` to inject a fake
session (mirrors `tests/test_agent_step.py::fake_bd`) is the
established stub surface.
