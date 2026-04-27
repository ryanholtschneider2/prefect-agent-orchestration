# Plan — prefect-orchestration-7hv

## Affected files
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/agent_session.py` — broaden `ClaudeCliBackend.run()` non-zero exit error to include stdout (and rendered argv).
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_agent_session.py` — **new** unit-test file housing the regression.

## Approach

`ClaudeCliBackend.run()` (lines ~165–168 of `agent_session.py`) currently raises:

```python
raise RuntimeError(
    f"claude CLI exited {proc.returncode}\nstderr: {proc.stderr[:2000]}"
)
```

The 2026-04-24 incident produced empty `stderr`, leaving zero diagnostic signal. Change the message to also include `stdout[:2000]` and the rendered argv, mirroring the format already used by `TmuxClaudeBackend` (line ~518 uses `stdout tail: {stdout[-2000:]}`):

```python
raise RuntimeError(
    f"claude CLI exited {proc.returncode}\n"
    f"argv: {cmd}\n"
    f"stderr: {proc.stderr[:2000]}\n"
    f"stdout: {proc.stdout[:2000]}"
)
```

The argv is built locally via `_build_claude_argv(...)` and contains no secrets (no env, no prompt — prompt is passed via stdin), so logging it is safe. `_clean_env` already strips sensitive bits from the env passed downstream, but env isn't included in the message.

Successful runs (`returncode == 0`) hit the `_parse_envelope(...)` return path unchanged → no behavior change on the happy path.

## Acceptance criteria (verbatim)

1. Non-zero exit RuntimeError includes stdout[:2000];
2. Regression test in tests/test_agent_session.py locks this;
3. No behavior change on successful runs.

## Verification strategy

- **AC1**: New unit test patches `subprocess.run` to return a `CompletedProcess` with `returncode=2`, `stderr=""`, `stdout="boom-on-stdout"`, asserts `RuntimeError` raised and `"boom-on-stdout"` substring in `str(exc)`.
- **AC2**: The test lives at `tests/test_agent_session.py` (per AC) and is exercised by `uv run python -m pytest tests/test_agent_session.py`.
- **AC3**: Second unit test patches `subprocess.run` to return `returncode=0` with a valid JSON envelope on stdout, asserts `ClaudeCliBackend().run(...)` returns `(result, session_id)` without raising.

## Test plan

- **unit** — only layer applicable. New `tests/test_agent_session.py` with two tests (failure path + happy path). Mocks `subprocess.run` (no real `claude` CLI required), placing it firmly in the unit layer per repo conventions.
- **e2e** — n/a (no CLI roundtrip change, no new flow surface).
- **playwright** — n/a (no UI).

Run: `uv run python -m pytest tests/test_agent_session.py -q`.

## Risks

- **Sensitive content leakage via stdout**: stdout from Claude could contain transcript fragments. Truncating to 2000 chars matches the existing stderr cap and the TmuxClaudeBackend convention; this is acceptable per the issue's design note.
- **argv exposure**: `_build_claude_argv` returns a plain argv list with model name, session id, and the `--dangerously-skip-permissions` flag — no API keys or env. Safe to include.
- **No API-contract change**: `RuntimeError` type and call signature unchanged; only the message string is broader.
- **Consumer breakage**: any caller string-matching the old error message (e.g. tests) would need to widen its assertion. A grep of `claude CLI exited` confirms only `agent_session.py` (lines 167, 519) emits this — no current consumer pattern-matches it.
