---
name: slack
description: Send messages, upload files, and add reactions to Slack via the po CLI or the official Slack tooling. Use when the user asks to post to Slack, share a file in a channel, react to a message, or set up a Slack notification flow.
---

# Slack skill

CLI-first guide for working with Slack from this rig. Two paths exist;
pick one based on the task:

## Path 1 (preferred for most tasks): `po slack-*` shipped helpers

The [`po-slack`](../../) tool pack ships three small commands that
wrap [`slack_sdk`](https://slack.dev/python-slack-sdk/). They're
already installed if this skill is visible — `po list` will show
them under `KIND=command`.

```bash
# Post a top-level message
po slack-post --channel '#general' --text 'deploy started'

# Reply in a thread (capture ts from a previous post)
po slack-post --channel '#general' --text 'deploy ok' --thread_ts 1700000000.000100

# Upload a file with optional title + initial comment
po slack-upload --channel '#general' --file ./report.pdf --title 'Q2 report' --comment 'See thread'

# Add a reaction (with or without the surrounding colons)
po slack-react --channel C0123456789 --ts 1700000000.000100 --name thumbsup
```

Auth: export `SLACK_BOT_TOKEN` (`xoxb-…`) before invoking. Run
`po doctor` to verify the token is present and the workspace is
reachable (`auth.test`).

## Path 2: official Slack CLI (Deno-based, for automation apps)

When you're building a full Slack automation app (Workflow Builder
custom steps, Run On Slack functions, triggers), use the official CLI
documented at:

- https://api.slack.com/automation/cli/
- https://api.slack.com/automation/quickstart

It's overkill for "send a message" — use Path 1 for that. Reach for
the Slack CLI when you need to:

- scaffold a TypeScript / Deno automation app (`slack create`)
- run a function locally against your workspace (`slack run`)
- deploy a hosted automation app (`slack deploy`)
- manage triggers (`slack trigger create / delete / list`)

## Reference

- Web API index: https://api.slack.com/docs
- `chat.postMessage`: https://api.slack.com/methods/chat.postMessage
- `files.upload` is **deprecated** — `po slack-upload` uses
  `files_upload_v2` under the hood. See
  https://api.slack.com/methods/files.completeUploadExternal for the
  raw v2 flow.
- `reactions.add`: https://api.slack.com/methods/reactions.add
- `auth.test` (used by `po doctor`): https://api.slack.com/methods/auth.test
- `slack_sdk` (Python) docs: https://slack.dev/python-slack-sdk/
- Vendor llms.txt: not currently published by Slack — fall back to the
  doc URLs above.

## Conventions in this rig

See `nanocorp-rules/slack.md` (laid down by the `po-slack` overlay)
for channel naming, @-mention etiquette, and the no-DMs-to-clients
rule. Read it before posting on behalf of the team.
