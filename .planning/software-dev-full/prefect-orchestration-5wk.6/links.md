# prefect-orchestration-5wk.6 — run handles

**Flow run id**: `673eb59a-958b-482d-adde-569833119e84`
**Run dir**: `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/.planning/software-dev-full/prefect-orchestration-5wk.6`

## Lurk (during run)

Attach to a role's tmux window:

```bash
tmux attach -t prefect-orchestration \; select-window -t prefect-orchestration-5wk.6-triager
tmux attach -t prefect-orchestration \; select-window -t prefect-orchestration-5wk.6-tester
tmux attach -t prefect-orchestration \; select-window -t prefect-orchestration-5wk.6-builder
tmux attach -t prefect-orchestration \; select-window -t prefect-orchestration-5wk.6-critic
tmux attach -t prefect-orchestration \; select-window -t prefect-orchestration-5wk.6-verifier
tmux attach -t prefect-orchestration \; select-window -t prefect-orchestration-5wk.6-cleaner
tmux attach -t prefect-orchestration \; select-window -t prefect-orchestration-5wk.6-documenter
tmux attach -t prefect-orchestration \; select-window -t prefect-orchestration-5wk.6-releaser
tmux attach -t prefect-orchestration \; select-window -t prefect-orchestration-5wk.6-demo-video
tmux attach -t prefect-orchestration \; select-window -t prefect-orchestration-5wk.6-learn
```

## Resume a Claude session

| role | session_id | history |
|---|---|---|
| triager | `bf5636fd-144e-446f-913b-62e479ea4f1b` | `/home/ryan-24/.claude/projects/-home-ryan-24-Desktop-Code-personal-nanocorps-prefect-orchestration/bf5636fd-144e-446f-913b-62e479ea4f1b.jsonl` |
| tester | `ab21a728-91ed-44bc-9a2d-984ce7b05076` | `/home/ryan-24/.claude/projects/-home-ryan-24-Desktop-Code-personal-nanocorps-prefect-orchestration/ab21a728-91ed-44bc-9a2d-984ce7b05076.jsonl` |
| builder | `765fe8b3-9dc5-4ace-ac63-bc0b405d8531` | `/home/ryan-24/.claude/projects/-home-ryan-24-Desktop-Code-personal-nanocorps-prefect-orchestration/765fe8b3-9dc5-4ace-ac63-bc0b405d8531.jsonl` |
| critic | `439c1e8e-73ae-4ede-a9c4-4b3ec4287599` | `/home/ryan-24/.claude/projects/-home-ryan-24-Desktop-Code-personal-nanocorps-prefect-orchestration/439c1e8e-73ae-4ede-a9c4-4b3ec4287599.jsonl` |
| verifier | — | — |
| cleaner | — | — |
| documenter | — | — |
| releaser | — | — |
| demo-video | — | — |
| learn | — | — |

Resume one outside the flow:

```bash
claude --print --resume <uuid> --fork-session
```

Or via PO: `po sessions prefect-orchestration-5wk.6 --resume <role>`

The `history` column points at Claude Code's local transcript JSONL — every assistant turn, tool call, and tool result the role made. Useful for post-mortem.
