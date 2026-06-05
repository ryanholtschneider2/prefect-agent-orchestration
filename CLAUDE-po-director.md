# po-director

**What it provides:** the **Director** — a heartbeat chief that watches the
directory it was started in, reads the goal + beads board, proposes the next
software-dev work, gates on human approval (`bd human` + Slack), and dispatches
approved work via `po run`. No `nanoc` dependency.

**When to use:**
- You want standing, proactive forward motion on a repo's work queue without
  hand-dispatching each bead.
- You want "it proposes work, I approve from Slack, it runs."

**Key verbs:** `po director start [DIR]`, `po director stop`,
`po director status`; formulas `director-pulse`, `director-reflect`.

**Key paths:** config `<workspace>/.director.toml`; goal `<workspace>/goal.md`;
memory `<workspace>/.director/handoff-<date>.md`; prompts
`po_director/agents/{director,reflector}/prompt.md`.

**Skip if:** you just want to dispatch one known bead — use `po run` directly.

**Read more:** `po show director-pulse`, `po-director/README.md`,
`~/.agents/plans/po-director-plan.md`.
