# Decision Log — `prefect-orchestration-4h8.4`

- **Decision**: When the Prefect flow is already terminal at startup,
  still spawn `_poll_run_dir`. Removed the prior `not terminal_on_start`
  guard that suppressed it.
  **Why**: AC4 explicitly says "gracefully degrades if only one source
  is available (e.g., run finished so no more state changes — still
  show new artifacts)". The unit test
  `test_run_watch_emits_live_run_dir_events` constructs exactly that
  scenario (Completed flow + tmp run_dir, then drops a `new.md`) and
  asserts the file appears in the merged feed. The previous guard made
  the test (and the AC) impossible to satisfy. The poller still exits
  cleanly because the test removes the run_dir, which `_poll_run_dir`
  already handles via the `not current and not run_dir.exists()` early
  return. In real CLI use, Ctrl-C cancels the task — the existing
  `KeyboardInterrupt → typer.Exit(0)` handler in `cli.watch` covers AC2.
  **Alternatives considered**: (a) leaving the guard and adding a
  separate "post-terminal short watch" loop with a deadline — rejected
  as accidentally complex; the existing `_poll_run_dir` already does the
  right thing. (b) Lowering the test's assertion to "Completed line
  appears" — rejected as gaming the test; the AC genuinely requires
  late-artifact visibility.
