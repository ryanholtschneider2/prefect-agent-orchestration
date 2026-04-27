# Plan: prefect-orchestration-7hv

## Summary
`ClaudeCliBackend.run()` previously raised `RuntimeError` containing only `stderr` on non-zero exit. In the 2026-04-24 incident, three concurrent builders crashed with empty stderr — undebuggable. The fix surfaces `stdout[:2000]` and the rendered `argv` alongside `stderr[:2000]` in the error message.

Commit `429a360` already landed this change. This plan documents the work for the build/critic/verify pipeline to confirm completeness against acceptance criteria.

## Affected files
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/agent_session.py` — error message format in `ClaudeCliBackend.run()` (lines ~166–170).
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_agent_session.py` — regression test covering non-zero exit error message (added in 429a360, ~59 lines).

## Approach
In `ClaudeCliBackend.run()`, on `proc.returncode != 0`:

```python
raise RuntimeError(
    f"claude CLI exited {proc.returncode}\n"
    f"argv: {cmd}\n"
    f"stderr: {proc.stderr[:2000]}\n"
    f"stdout: {proc.stdout[:2000]}"
)
```

`cmd` is the rendered argv list from `_build_claude_argv`. No tokens / secrets are injected into argv (only `--print --output-format json --resume <uuid>` and similar flags), so logging it is safe per triage. Truncation at 2000 chars bounds error-message size in Prefect logs.

Successful path is unchanged — the check is gated on `returncode != 0`.

## Acceptance criteria (verbatim)
1. Non-zero exit RuntimeError includes stdout[:2000];
2. Regression test in tests/test_agent_session.py locks this;
3. No behavior change on successful runs.

## Verification strategy
- **AC1**: Inspect `prefect_orchestration/agent_session.py` `ClaudeCliBackend.run()` non-zero branch — confirm `stdout[:2000]` substring is in the raised message and includes `argv`.
- **AC2**: `uv run python -m pytest tests/test_agent_session.py -k "nonzero or stdout or runtime_error" -v` — confirm regression test exists, exercises a non-zero exit (likely via monkeypatched `subprocess.run`) with non-empty stdout, and asserts the message contains stdout content + argv.
- **AC3**: Run the full unit test layer (`uv run python -m pytest tests/ --ignore=tests/e2e --ignore=tests/playwright`) — successful-path tests in `test_agent_session*.py` should still pass.

## Test plan
- **unit**: primary — `tests/test_agent_session.py` regression test for non-zero exit; existing happy-path tests confirm no behavior change.
- **e2e**: not required (pure error-formatting change in a backend; already gated by `.po-env PO_SKIP_E2E=1` for this rig).
- **playwright**: N/A (no UI).

## Risks
- **Argv leakage**: confirmed safe — `_build_claude_argv` constructs from `start_command` + `--print`, `--output-format json`, `--model`, `--resume <uuid>`, `--fork-session`. No env tokens, no prompt content, no API keys.
- **Truncation cap**: 2000 chars per stream is sufficient for Claude CLI error envelopes; larger payloads are unusual and the cap protects log volume.
- **API contract**: `RuntimeError` type is unchanged; only the message string is enriched. Callers that rely on the type continue to work; any caller string-matching the old message would break, but a grep shows none in-tree.
- No migrations, no breaking consumers downstream.

## Note on baseline failures
The baseline shows 25 pre-existing failures (mail prompt path, overlay materialization, tmux argv, packs CLI, deployments). None are related to `ClaudeCliBackend.run()` non-zero error formatting. Verifier should compare post-change failures against the baseline set and not gate on those.
