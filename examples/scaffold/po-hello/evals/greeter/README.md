# `greeter` agent evals

Eval suite for the `greeter` operating agent (scaffolded by `po new agent`).
Every PO agent ships with evals — this is the durable form of the lessons the
agent learns when a human steps in.

## Run

```bash
# from agent-evals-best-practices, with the aeval package:
PO_BACKEND=tmux aeval run --suite . --judge-model claude-code
```

- **Backend:** drive the agent-under-test on the tmux backend (`PO_BACKEND=tmux`)
  so the eval matches the lurkable production runtime; attach mid-turn to watch.
- **Judge:** `claude-code` over OAuth (`~/.claude/.credentials.json`). Do NOT set
  `ANTHROPIC_API_KEY` for eval runs — the SDK spawns the Claude CLI and the key
  would override OAuth.
- **Cases come from real-world results.** Seed `cases.yaml` from concrete
  scenarios, then grow it from production transcripts and every human escalation.

See `~/Desktop/Code/personal/agent-evals-best-practices/` for the runner and the
case/rubric schema.
