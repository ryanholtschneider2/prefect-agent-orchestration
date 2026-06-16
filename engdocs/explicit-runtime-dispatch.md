# Explicit Runtime Dispatch

PO treats runtime selection as a dispatch decision, not a complexity router.
The dispatching agent supplies four independent fields:

1. backend (`cursor-*`, `codex-*`, or Claude `cli`/`tmux`)
2. account or account class
3. model
4. effort

Preferred order is Cursor, then Codex, then Claude when the earlier provider
reports exhaustion or cannot perform the work. There is no speculative
cross-provider fan-out and no automatic provider failover inside a run.

| Work class | Preferred runtime |
|---|---|
| Default | Cursor `composer-2.5`; effort field `medium` |
| Sonnet substitute | Codex `gpt-5.4`, medium |
| Difficult | Codex `gpt-5.5`, high |
| Exceptional | Codex `gpt-5.5`, xhigh or max |
| Fallback | Claude Sonnet, medium; Opus/high only when justified |

Composer 2.5 does not currently expose a separate effort knob. PO keeps the
common effort field in the dispatch contract, but the Cursor backend ignores
it while using Composer.
