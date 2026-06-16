# po-director

**What it provides:** the **Director** — a heartbeat chief that watches the
directory it was started in, reads the goal + beads board, proposes the next
software-dev work, gates on human approval (`bd human` + Slack), and dispatches
approved work via `po run`. No `nanoc` dependency.

**When to use:**
- You want standing, proactive forward motion on a repo's work queue without
  hand-dispatching each bead.
- You want "it proposes work, I approve from Slack, it runs."

**Key verbs:** `po director start [DIR] [--persona NAME]`, `po director stop`,
`po director status`; formulas `director-pulse` (20m execution), `director-roadmap`
(hourly planning → ROADMAP.md + beads), `director-report` (nightly),
`director-dream`, `director-improve`.

**Personas:** the standing agent's identity is configurable (`--persona`,
`persona=` in `.director.toml`, or `[persona].name` in `.ade/settings.toml`).
Default `director` is builtin; packs ship more via the `po.personas` entry-point
group. Non-default personas suffix deployment/session names so several can share
one workspace. See README "Personas".

**Key paths:** config `<workspace>/.director.toml`; goal `<workspace>/goal.md`;
roadmap `<workspace>/ROADMAP.md` + `<workspace>/.director/roadmap-tldr.md`;
memory `<workspace>/.director/handoff-<date>.md`; prompts
`po_director/agents/{director,roadmapper,reporter,dreamer,improver}/prompt.md`.

**Skip if:** you just want to dispatch one known bead — use `po run` directly.

**Read more:** `po show director-pulse`, `po-director/README.md`,
`~/.agents/plans/po-director-plan.md`.
