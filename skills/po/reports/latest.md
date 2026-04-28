# skill-evals: prefect-orchestration / po

- **judge_model**: `openai:gpt-5-mini`
- **tier**: `(all)`
- **pass threshold**: 0.75
- **result**: 9/9 passed (PASS)
- **started**: 2026-04-28T23:28:38Z
- **finished**: 2026-04-28T23:29:50Z

| case | tier | score | pass | criteria |
|---|---|---|---|---|
| smoke-what-is-po | smoke | 1.00 | PASS | responds-with-content=1.00, mentions-core-vocabulary=1.00 |
| smoke-recommend-command-for-issue | smoke | 1.00 | PASS | responds-with-content=1.00, recommends-correct-subcommand=1.00, includes-rig-flags=1.00 |
| smoke-po-vs-prefect-deference | smoke | 1.00 | PASS | responds-with-content=1.00, defers-to-prefect-cli=1.00 |
| tier1-dispatch-bead | regression | 1.00 | PASS | responds-with-content=1.00, recommends-correct-subcommand=1.00, includes-rig-flags=1.00, no-todowrite=1.00 |
| tier1-fan-out-epic | regression | 1.00 | PASS | responds-with-content=1.00, recommends-correct-subcommand=1.00, includes-rig-flags=1.00 |
| tier1-retry-stuck-run | regression | 1.00 | PASS | responds-with-content=1.00, recommends-correct-subcommand=1.00 |
| tier1-see-role-sessions | regression | 1.00 | PASS | responds-with-content=1.00, recommends-correct-subcommand=1.00 |
| tier1-inspect-verdicts | regression | 1.00 | PASS | responds-with-content=1.00, recommends-correct-subcommand=1.00 |
| tier1-cancel-flow-run-defers-to-prefect | regression | 1.00 | PASS | responds-with-content=1.00, defers-to-prefect-cli=1.00, no-invented-po-verbs=1.00 |
