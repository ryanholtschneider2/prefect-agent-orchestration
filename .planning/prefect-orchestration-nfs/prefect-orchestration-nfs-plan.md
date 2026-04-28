# Plan: prefect-orchestration-nfs

**Bug**: Two parallel `po run software-dev-full` invocations against the same rig (PO_BACKEND=tmux): one finishes ~8 min, the other wedges at the very first triager step — empty tmux pane, no Claude child, empty `verdicts/`, idle indefinitely.

## Diagnosis

Triager is **the first step of a fresh run** → the `TmuxInteractiveClaudeBackend.run()` "fresh" branch (`prior is None and not fork`). Sequence today:

1. `_ensure_stop_hook(cwd)` — already concurrency-safe (fcntl + atomic write, fix `4a80e0e`). Not the suspect.
2. `_spawn_tmux(...)` — distinct session names per spawn. Safe.
3. `_wait_for_tui_ready(target, fallback_s=8.0)` — polls tmux pane for `❯` glyph. **On non-detection it returns silently** (no signal that the TUI never rendered).
4. Pane-tail check for `[claude exited` — catches crashes only.
5. `tmux load-buffer` + `paste-buffer -p -d` + 3× `send-keys Enter`, looking for `active_markers` (`esc to interrupt`, `Composing`, etc.). **If none seen, falls through silently.**
6. `_wait_for_stop` / `_discover_resumed_sentinel` — blocks until `timeout_s` (1800s default). The wedge.

**Most likely root cause**: when two `claude --dangerously-skip-permissions ...` processes spawn within ~milliseconds in the same `cwd`, one of them races on shared Claude Code on-disk state — credentials read, project-slug mkdir at `~/.claude/projects/<slug>/`, OAuth token refresh, or the TUI's settings merge — and never reaches a usable input prompt. The wrapper's trailing `; sleep infinity` keeps the pane alive (so we don't see "[claude exited"); the pane just stays empty (no `❯` glyph, no exit marker). Steps 3–5 trundle through without raising, and step 6 polls forever.

Even if the root cause is something we can't fully solve from outside Claude Code, **the orchestrator should never wedge for 23 min on this** — the acceptance criterion explicitly accepts a clear, actionable error within 60 s as a valid resolution.

## Acceptance criteria mapping

> Two parallel `po run software-dev-full` against same rig both progress past triager 99% of the time, **OR** a clear, actionable error fires within 60 s when wedge detected.

We deliver **both**:
- **Fix A — detection**: surface a wedge as a `RuntimeError` (with pane-tail diagnostics) within ~30–45 s of the symptom appearing, instead of polling the sentinel for `timeout_s` (default 1800 s). Satisfies the OR clause.
- **Fix B — root-cause mitigation**: serialize the *initial* Claude-CLI spawn per-rig with an advisory `flock` so two parallel runs don't race on Claude's startup-time on-disk state. Aims for the 99% target. Lock is held only for the brief spawn-and-tui-ready window (~3–10 s) — not for the multi-minute work turn.

Either fix alone would satisfy the criterion; together they make the system both robust and friendly.

## Scope

`prefect_orchestration/agent_session.py` only. Two new helpers, three call-site changes inside `TmuxInteractiveClaudeBackend.run()`. Plus tests. No prompt changes, no formula changes, no new modules.

## Implementation

### 1. `_wait_for_tui_ready` returns a status

Today it returns `None` whether the glyph appeared or not. Change signature to return `bool` (`True` = saw `❯`/`Welcome back`, `False` = neither glyph nor exit marker — likely wedged startup). Existing callers ignore the value; they still work. The new caller in `TmuxInteractiveClaudeBackend.run()` reads it.

### 2. New helper `_assert_submission_landed(target, deadline_s) -> None`

After the existing 3× `send-keys Enter` loop, if no `active_marker` was observed, give claude one final grace window (e.g. 30 s of poll) checking the pane every 1 s for any active marker OR for the rate-limit dialog OR for the `[claude exited` marker. If none of those appear by the deadline, raise `RuntimeError` whose message uses the existing `_format_wedge_error(...)` shape so operators see a familiar, actionable error.

Total worst-case latency from spawn to wedge-error: `_wait_for_tui_ready` 8 s + paste retries ~9 s + grace window 30 s = ~47 s, well under the 60 s SLA.

### 3. New helper `_with_rig_spawn_lock(cwd) -> contextmanager`

```python
@contextmanager
def _with_rig_spawn_lock(cwd: Path) -> Iterator[None]:
    """Serialize per-rig Claude CLI spawns to dodge startup-time races.

    Two parallel `claude --dangerously-skip-permissions` processes in the
    same cwd race on Claude Code's startup state (credentials read,
    project-slug mkdir, OAuth refresh) and one wedges with an empty TUI.
    A short fcntl lock around _spawn_tmux + _wait_for_tui_ready
    serializes the brief startup window. Released before the work turn
    begins so concurrency for the actual agent work is unaffected.
    """
```

Stored at `<cwd>/.planning/.po-claude-spawn.lock`. Lock acquired with `fcntl.LOCK_EX`, released as soon as `_wait_for_tui_ready` returns. Honor a `PO_DISABLE_SPAWN_LOCK=1` env var so we can A/B-test or disable in CI/stub paths. Stub backend doesn't go through this code so no test impact there.

### 4. Wire-up in `TmuxInteractiveClaudeBackend.run()`

```python
with _with_rig_spawn_lock(cwd):
    target = _spawn_tmux(...)
    tui_ready = _wait_for_tui_ready(target, fallback_s=self.settle_s)
# … existing pane checks unchanged …
# After the 3× send-keys Enter loop, before the wait_for_stop block:
if not submission_seen:
    _assert_submission_landed(
        target, deadline_s=30.0,
        active_markers=active_markers,
        issue=self.issue, role=self.role,
        session_id=new_sid, timeout_s=self.timeout_s or 0.0,
    )
```

Track `submission_seen` inside the existing for-loop (set True on first `any(m in pane for m in active_markers)`).

The `tui_ready` return value isn't strictly needed for the wedge-detection (the post-paste check covers it), but we log a one-line warning when `False` so the operator can correlate.

## Verification Strategy

| Criterion | Verification Method | Concrete Check |
|-----------|---------------------|----------------|
| Wedge surfaces as RuntimeError within 60 s | unit test (mock tmux/subprocess) | `test_wedge_detection_raises_within_grace_window` — patches `subprocess.run` so `tmux capture-pane` always returns the empty input prompt; calls `TmuxInteractiveClaudeBackend.run()`; asserts `RuntimeError` raised within ~45 s wall-clock with pane-tail in message |
| Per-rig spawn lock serializes concurrent spawns | unit test | `test_spawn_lock_blocks_concurrent` — two threads enter `_with_rig_spawn_lock(cwd)` for same cwd; second must block until first exits; different `cwd` values don't block each other |
| Spawn lock can be disabled | unit test | `PO_DISABLE_SPAWN_LOCK=1` makes `_with_rig_spawn_lock` a no-op (verified via timing + thread interleaving) |
| Existing behavior preserved when submission lands normally | unit test | `test_submission_landed_skips_grace_window` — patches pane to show `Composing` after first Enter; assert no extra delay introduced |
| Live e2e | manual smoke (post-merge) | Two parallel `po run software-dev-full --issue-id X --rig … --rig-path /tmp/po-dummy-rig` invocations both reach plan iteration 1 within 60 s; OR if either wedges, error is raised within 60 s and `po retry` succeeds |

The live smoke is documented in the verification report; we won't gate the merge on it (requires a running Prefect server + spare beads + ~10 min wall-clock × multiple runs to get confidence on a probabilistic race).

## Decision log items expected from builder

- Why `flock` (file lock) vs `threading.Lock` — multi-process concurrency, two `po run` invocations are independent processes
- Why grace window = 30 s — keeps total wedge-detection latency under 60 s while tolerating slow-startup tail of normal claude launches
- Why per-rig lock instead of per-host — avoids serializing `po run`s in different rigs
- Why log-only when `tui_ready=False` instead of raising immediately — false-negative on TUI glyph detection (e.g. terminal width quirks) shouldn't tank an otherwise-healthy run; the post-paste detection is the firm gate

## Out of scope

- Investigating *which* Claude CLI startup-state file is the contended one. The lock dodges the question; if a future Claude CLI release fixes the race we can drop the lock by env-var.
- Changing `TmuxClaudeBackend` (the non-interactive `--print` variant). Bug is reported only against `TmuxInteractiveClaudeBackend` (the default).
- Detecting wedges *during* a long turn (covered by `timeout_s` / sav.1).
