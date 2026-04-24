# prefect-orchestration TODO

Post-`sr-8yu.3` validation. First real run succeeded end-to-end in 17 min
(happy path, no loops triggered). Next chunk of work below.

## 1. Refactor verdicts: JSON-in-reply → file artifacts `[in progress]`

Current design parses trailing ` ```json ` blocks out of agent replies
(`last_json_block`). Wrong abstraction — the orchestrator should read
the world the agent changed, not parse the agent's prose.

- Replace with: agent writes `$RUN_DIR/verdicts/<step>-iter-N.json` via
  a shell `echo ... > ...` at the end of its turn. File existence is
  the step signal; the JSON content is trivially parseable because the
  agent controls the exact bytes.
- Affected prompts: `triager`, `critique_plan`, `review`, `verification`,
  `ralph`, `test`, `regression_gate` (7).
- Affected tasks: same set — swap `last_json_block(reply)` for
  `read_verdict(run_dir, "<step>-iter-N")`.
- Drop `parsing.last_json_block`; it's redundant once this lands.

## 2. `po epic <epic-id>` — topo-sort beads children into a Prefect DAG `[in progress]`

Beads dependencies map 1:1 onto Prefect's `wait_for=`. No polling, no
daemon. When `bd list --parent=<epic-id>` changes, re-invoke — idempotent
claim logic skips already-closed children.

- Read `bd list --parent=<epic-id> --status=open,in_progress --json`.
- Build dep graph from each child's `dependencies[]` (scoped to this epic).
- Topo-sort; submit `software_dev_full.submit(...)` per child with
  `wait_for=[parent_run.result()]` chained through a dict of futures.
- Tag the flow runs with `{epic_id, issue_id}` for UI filtering.

## 3. Claim-on-enter / close-on-exit `[in progress]`

- At flow start (after triage dir setup): `bd update <issue_id> --status=in_progress --assignee=po-{flow_run_id}`.
- At flow end (after `learn`): `bd close <issue_id>`.
- Skip gracefully if `bd` not on PATH.

## 4. Concurrency docs `[pending]`

README section only, no code:

```bash
# Total concurrent issues in the pool ("max-workers"):
prefect work-pool create po --type process --concurrency-limit 4

# Per-role caps (across all flow runs):
prefect concurrency-limit create critic 2
prefect concurrency-limit create builder 3
# then tag tasks: @task(tags=["critic"])
```

Also add `tags=["<role>"]` to each `@task` in `software_dev.py` so the
global tag limits actually bite.

## 5. `TmuxClaudeBackend` — lurk-able sessions `[pending]`

New backend class: spawns `claude --print` inside `tmux new-session -d -s po-{issue}-{role}` with stdout tee'd. Envelope parsing stays the same (`--output-format json` still works through tee). User can `tmux attach po-sr-8yu.3-builder` to watch a turn happen live.
