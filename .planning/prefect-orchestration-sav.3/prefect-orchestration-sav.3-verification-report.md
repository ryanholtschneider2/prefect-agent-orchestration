# Verification Report: prefect-orchestration-sav.3

## Acceptance Criteria

| # | Criterion | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | After `po retry <id>`, prior tmux session for that issue is killed | Unit test | PASS | `tests/test_retry.py::test_retry_kills_prior_tmux_for_issue` — patches `tmux_tracker.kill_for_issue`, asserts called with the issue id before archive/relaunch |
| 2 | SIGINT to `po run` cleans up tmux sessions + child claude before exit | Unit test | PASS | `tests/test_cli_run_signal.py::test_run_handler_kills_tmux_tracker_on_signal` — captures the installed handler, invokes it, asserts `kill_all` ran and `typer.Exit(128+SIGINT)` raised |
| 2b | Signal handler installed only during the flow, restored after | Unit test | PASS | `tests/test_cli_run_signal.py::test_run_installs_and_restores_signal_handlers` |
| 3 | After hung-flow + retry + completion, no leftover tmux/claude for the issue | Tracker logic | PASS | `tests/test_tmux_tracker.py::test_kill_for_issue_unscoped_session` + `test_kill_for_issue_scoped_window` + `test_kill_for_issue_dot_sanitization` — verify `po-{safe_issue}-*` sessions and `{safe_issue}-*` windows in scoped `po-{rig}` sessions are killed (and dots are mapped to underscores so `prefect-orchestration-sav.3` → `prefect-orchestration-sav_3` matches Tmux's actual session names) |
| 4 | `sleep infinity` only fires on abnormal claude exit (the proximate cause of the 11h zombie) | Source-inspection unit test | PASS | `tests/test_agent_session_tmux.py::test_interactive_wrapper_sleep_infinity_only_on_failure` — asserts the `if [ "$rc" -ne 0 ]` guard is present and the legacy unconditional shape is gone |

## Regression Check
- Baseline (pre-change): 10 failed, 623 passed, 2 skipped
- Final: 10 failed, 654 passed, 2 skipped
- Same 10 baseline failures persist (test_cli_packs/* — unrelated uv-tool harness; test_deployments::test_po_list_still_works — pack registration; test_mail::test_prompt_fragment_exists_and_mentions_inbox — missing pack file `po_formulas/mail_prompt.md`; test_agent_session_tmux::test_session_name_derivation — pre-existing dot-handling drift unrelated to this issue).
- Net: +31 passing (new tests), 0 regressions.

## Live Environment Verification
- Environment: NONE (cannot exercise SIGINT-mid-flow without spawning a real claude turn — that would burn rate-limit slots and require the bug to actually reproduce).
- Behavior verified via inspection-based unit tests of the wrapper string and signal-handler installation.

## Decision Log Review
- 5 decisions logged. None deviate from plan.

## Confidence Level
- **MEDIUM**: All criteria verified via deterministic unit tests including:
  - tracker correctness against multiple session-naming layouts (scoped + unscoped + dot-sanitized),
  - signal-handler install/restore semantics,
  - retry's pre-cleanup invocation + non-fatal-on-failure contract,
  - source inspection of the conditional wrapper.
  Live reproduction of "kill po, observe no zombie" is the cleanest end-to-end check but requires a real claude session and tmux server in a controlled state. Operator should confirm on the next real run that:
  1. `po retry <id>` after a hung flow finds no leftover `po-<id>-*` sessions afterward.
  2. Ctrl-C on `po run` echoes `[po] killed N tmux session(s)/window(s) on signal 2` before exit.
