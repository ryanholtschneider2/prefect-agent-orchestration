# Plan: prefect-orchestration-64y — TmuxClaudeBackend

## Affected files

- `prefect_orchestration/agent_session.py` — add `TmuxClaudeBackend` dataclass alongside `ClaudeCliBackend`/`StubBackend`. Imports: add `os`, `tempfile`, `time`. No changes to `SessionBackend` Protocol or `AgentSession` (backend is swappable via `AgentSession(backend=...)`).
- `tests/test_agent_session_tmux.py` — new unit tests (command construction, session-name derivation, verdict-file write-through, cleanup). Tests that actually spawn tmux are gated on `shutil.which("tmux")` / env flag.
- `pyproject.toml` — no new deps (tmux is a system binary; we `subprocess.run` it).

## Approach

Reuse the exact `claude --print --output-format json [...]` command built by `ClaudeCliBackend` and wrap it in a tmux invocation so a human can attach mid-run. Key design choices grounded in the existing code:

1. **Factor out command construction.** Extract the arg-building block at `agent_session.py:67-80` into a small helper (`_build_claude_argv(start_command, session_id, fork, model)`) so both backends produce the identical Claude CLI call. Prevents drift.

2. **Run Claude through tmux, capture stdout via a pipe file.** tmux owns the PTY so `capture_output=True` on the outer `subprocess.run` gives nothing useful. Approach:
   - Compute `session_name = f"po-{issue}-{role}"`. Since `(issue, role)` isn't passed into `SessionBackend.run`, derive `issue` + `role` by adding two optional fields to `TmuxClaudeBackend` (`issue: str`, `role: str`) that `AgentSession` populates OR — simpler — let the caller construct `TmuxClaudeBackend(issue=..., role=...)` directly (the `AgentSession(backend=TmuxClaudeBackend(...))` form explicitly called out in the acceptance criteria). Go with the explicit form.
   - Write a small wrapper shell snippet that:
     - runs the claude argv with stdin from a temp file (the prompt),
     - tees stdout to a capture file (`run_dir/.tmux/<session>.out`),
     - writes the exit code to `<session>.rc`.
   - Spawn via `tmux new-session -d -s <name> -x 200 -y 50 'bash -c "<wrapper>"'`.
   - Poll for the `.rc` file (bounded backoff, no tight loop) instead of `tmux wait-for`, which is fiddly across tmux versions. Also watch `tmux has-session` so we detect kill-session.
   - On completion, read `.out` and parse the same JSON envelope `ClaudeCliBackend` does; fall back to raw stdout if parse fails.

3. **Cleanup & orphans.** Wrap the spawn/poll in `try/finally` that runs `tmux kill-session -t <name>` (ignoring "session not found"). For pre-existing sessions with the same name: kill and recreate (prior run probably crashed) — log a warning via `print` to stderr. Do not attempt to reuse, per triage risk note.

4. **Env propagation.** Pass `env=os.environ.copy()` minus `ANTHROPIC_API_KEY` (matches user-global rule that the Claude CLI must use OAuth, not the key). `cwd=cwd` on the outer `subprocess.run` matches `ClaudeCliBackend`; the wrapper `cd`s to that cwd so verdict files land in `run_dir/verdicts/` exactly as today.

5. **Resume.** `session_id` handling is identical to `ClaudeCliBackend` (reuses helper from step 1). Each `.prompt()` call spawns a fresh tmux session of the same name — the prior one has already been killed by the `finally`. Persistent naming gives the human a stable target to `tmux attach` between turns.

6. **Exit-code propagation.** Sentinel file (`.rc`) populated by the wrapper. Non-zero → raise `RuntimeError` with stderr tail, matching `ClaudeCliBackend`'s error path.

### Sketch

```python
@dataclass
class TmuxClaudeBackend:
    issue: str
    role: str
    start_command: str = "claude --dangerously-skip-permissions"
    attach_hint: bool = True  # print `tmux attach -t <name>` on start

    def run(self, prompt, *, session_id, cwd, fork=False, model="opus"):
        name = f"po-{self.issue}-{self.role}"
        workdir = cwd / ".tmux"
        workdir.mkdir(parents=True, exist_ok=True)
        out_path = workdir / f"{name}.out"
        rc_path = workdir / f"{name}.rc"
        prompt_path = workdir / f"{name}.in"
        # ... write prompt, clear stale out/rc, kill stale session ...
        argv = _build_claude_argv(self.start_command, session_id, fork, model)
        wrapper = (
            f"cd {shlex.quote(str(cwd))} && "
            f"{shlex.join(argv)} < {shlex.quote(str(prompt_path))} "
            f"2>&1 | tee {shlex.quote(str(out_path))}; "
            f"echo ${{PIPESTATUS[0]}} > {shlex.quote(str(rc_path))}"
        )
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", name, "bash", "-lc", wrapper],
            check=True, env=_clean_env(),
        )
        if self.attach_hint:
            print(f"[tmux] attach with: tmux attach -t {name}", flush=True)
        try:
            _wait_for_rc(rc_path, name)  # polls, with timeout
            rc = int(rc_path.read_text().strip())
            stdout = out_path.read_text()
            if rc != 0:
                raise RuntimeError(f"claude CLI exited {rc}\nstdout tail: {stdout[-2000:]}")
            return _parse_envelope(stdout, session_id)
        finally:
            subprocess.run(["tmux", "kill-session", "-t", name],
                           check=False, stderr=subprocess.DEVNULL)
```

## Acceptance criteria (verbatim)

1. TmuxClaudeBackend in agent_session.py implementing SessionBackend Protocol
2. named tmux session per (issue,role)
3. `tmux attach -t po-sr-8yu.3-builder` shows live Claude output
4. verdict files still land in run_dir/verdicts/
5. `--resume <uuid>` works across turns
6. tmux session exits cleanly (no orphans)

## Verification strategy

| AC | How it's checked |
|----|------------------|
| 1 | Unit test: `isinstance(TmuxClaudeBackend(issue="x", role="y"), SessionBackend)` is structurally satisfied (duck check: `hasattr(..., 'run')` + signature via `inspect`). Import succeeds. |
| 2 | Unit test intercepts `subprocess.run` via monkeypatch and asserts the `tmux new-session -d -s po-{issue}-{role}` argv. |
| 3 | Manual/smoke test with tmux-available marker: start backend with a trivial prompt, `tmux list-sessions` inside test shows `po-<issue>-<role>`, `tmux capture-pane -p -t <name>` returns non-empty output containing claude CLI output. Gated behind `@pytest.mark.skipif(shutil.which("tmux") is None or not os.environ.get("PO_TMUX_E2E"))`. |
| 4 | Unit test with a `StubBackend`-style wrapper: swap the inner claude argv for `bash -c 'mkdir -p verdicts && echo {} > verdicts/plan.json'` via an injected `start_command`; assert `run_dir/verdicts/plan.json` exists after `.run()`. |
| 5 | Unit test: call `.run()` twice with a fake claude that echoes `{"session_id": "<uuid>", "result": "ok"}`; assert the second call's argv contains `--resume <uuid>`. |
| 6 | Unit test: after `.run()` returns (success *and* on raised exception via a failing fake claude), assert `tmux has-session -t <name>` returns non-zero. Gated on tmux available. |

## Test plan

- **Unit** (primary): monkeypatched `subprocess.run` for argv construction, session-name, env scrubbing, resume handling, envelope parsing, error propagation.
- **Integration (tmux-gated)**: real tmux + a fake `claude` shim on PATH (shell script that prints a JSON envelope and exits 0) — exercises AC 3, 4, 6 end-to-end without burning real API calls. Skipped when tmux absent or `PO_TMUX_E2E` unset.
- **Playwright/e2e**: N/A — no UI, no HTTP surface.

## Risks

- **No API contract change.** `SessionBackend.run` signature is preserved. `TmuxClaudeBackend` adds required `issue`/`role` fields to its own dataclass; existing `AgentSession(backend=ClaudeCliBackend())` default is untouched.
- **No migrations.**
- **Breaking consumers**: none — `ClaudeCliBackend` and `StubBackend` unchanged. If I factor `_build_claude_argv` out, I must ensure `ClaudeCliBackend.run` still produces byte-identical argv (covered by a regression unit test).
- **Platform risk**: tmux availability. Mitigated by raising a clear `RuntimeError` at `.run()` time if `shutil.which("tmux") is None`, rather than at import. Keeps CI green on tmux-less runners as long as nothing instantiates the backend.
- **Session-name collision across concurrent runs** (flagged in triage): acceptance criteria specifies only `(issue, role)`, so documented behavior is "kill and replace". If concurrent same-role runs become a real scenario, file a follow-up to add a turn/PID suffix.
- **Prompt leakage via temp file**: `.tmux/<session>.in` contains the full prompt on disk under `run_dir`. Same trust boundary as `run_dir/verdicts/*`, so acceptable. Cleanup of the `.in` file on success.
