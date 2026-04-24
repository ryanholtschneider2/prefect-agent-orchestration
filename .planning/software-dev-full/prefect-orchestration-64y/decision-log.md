# Decision log — prefect-orchestration-64y (build iter 1)

- **Decision**: Use `bash -lc <wrapper>` with `tee` + `${PIPESTATUS[0]}` into a sentinel `.rc` file, plus a polling loop on the rc file to detect completion.
  **Why**: tmux owns the PTY, so the outer `subprocess.run(capture_output=True)` on `tmux new-session` yields nothing. Plan §2 specifies this approach — poll rc file (portable across tmux versions) instead of `tmux wait-for` (fiddly across tmux versions).
  **Alternatives considered**: `tmux pipe-pane -o` (fragile; depends on pane still existing at read time); `tmux wait-for` (needed explicit channel-notify plumbing inside the wrapper); `script` wrapper (adds a dependency on `util-linux` `script` and adds ANSI noise to captured output).

- **Decision**: `issue` and `role` are required fields on `TmuxClaudeBackend`, not plumbed through `SessionBackend.run(**kwargs)`.
  **Why**: Plan §2 explicitly chose the "AgentSession(backend=TmuxClaudeBackend(issue=..., role=...))" form called out in AC. Keeps the `SessionBackend` Protocol unchanged → no breaking consumers (plan Risks §).
  **Alternatives considered**: Add `issue`/`role` kwargs to the Protocol (would break every backend and every caller); derive session name from `cwd` (ambiguous when multiple roles share a repo).

- **Decision**: Extract `_build_claude_argv` / `_parse_envelope` / `_clean_env` helpers at module level and reuse from `ClaudeCliBackend`.
  **Why**: Plan §1 + Risks §: prevent drift between the two backends and make argv assertions testable without spawning processes. Regression test `test_claude_cli_backend_argv_unchanged` locks the original argv in place.
  **Alternatives considered**: Duplicate the arg-building inside `TmuxClaudeBackend` (would drift).

- **Decision**: Kill-and-replace pre-existing tmux sessions with the same name, with a stderr warning.
  **Why**: Plan §3 / Risks §: AC only keys sessions by `(issue, role)`. Reusing a stale session from a crashed prior run risks mixing state; refusing would block retries after a crash.
  **Alternatives considered**: Refuse to start (painful for retries); append PID/turn suffix (violates the stable-name attach UX in AC 3).

- **Decision**: Integration tests use a fake `claude` shim script injected via `start_command=<path>` rather than monkeypatching `subprocess.run` inside tmux.
  **Why**: tmux spawns the wrapper in a child shell; monkeypatch in the parent Python process doesn't reach it. A real shim exercises the full spawn/tee/rc/kill flow end-to-end on a machine with tmux (covers ACs 2–6). Plan Test plan §Integration endorses this.
  **Alternatives considered**: Mock out tmux entirely (would not verify ACs 3 or 6).

- **Decision**: Strip `ANTHROPIC_API_KEY` in `_clean_env` for the tmux subprocess env.
  **Why**: User-global CLAUDE.md: "do NOT have `ANTHROPIC_API_KEY` set in the shell when running Claude CLI; it would override OAuth." Same convention applies when spawning via tmux.
  **Alternatives considered**: Pass env through untouched (would silently break subscription-based auth on developer laptops where the key is set for other services).

- **Decision**: Second-turn resume test spawns a fresh `TmuxClaudeBackend` with a different shim rather than reusing a single shim with an append log.
  **Why**: Initial append-based test failed because the kill-session teardown + re-spawn sequence races with the shim's second-turn write (timing-sensitive on a single log). Two backends / two logs is trivially deterministic.
  **Alternatives considered**: Sleep between turns (flaky); sync via flock (overkill for a unit test).

- **Decision**: `_wait_for_rc` bails if `tmux has-session` fails before the rc file materializes.
  **Why**: Plan §2 — a human can `tmux kill-session` mid-run; we want a clean RuntimeError rather than a timeout.
  **Alternatives considered**: Timeout only (delays failure by `timeout_s`).
