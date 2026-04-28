# Lessons Learned: prefect-orchestration-nfs

## What went well
- Existing `_ensure_stop_hook` (4a80e0e) and `_format_wedge_error` (sav.1) provided proven patterns to follow — the spawn lock is a direct sibling of the settings lock, and the wedge error reuses the existing diagnostic builder.
- Forward-referencing `_format_wedge_error` from `_assert_submission_landed` kept the new helper near its caller (`_wait_for_tui_ready`) without restructuring the file.

## Difficulties / friction
- During baseline capture, hit the *exact bug under investigation*: pytest hung indefinitely because of competing `po run` invocations in parallel sibling sessions. Killed and re-ran with explicit `--ignore=tests/e2e --ignore=tests/playwright` to bypass cross-session interference. Real-world feel for the wedge.
- Bytes literal with em-dash (`—`) caused a Python syntax error in tests; needed `.encode()` instead. Note for future: only ASCII inside `b"…"` literals.

## Patterns / takeaways
- **Pattern**: when shipping a fix for a parallel-resource race, ALWAYS add a per-process flock at a well-known path under `.planning/` (or analogous repo-local dir). Don't reach for `threading.Lock` (process-local) or `multiprocessing.Lock` (heavier, requires shared memory). `fcntl.flock` is the right primitive.
- **Pattern**: any "wait for external signal" loop in PO must have a *finite* deadline AND a clear-error path on timeout. The 23-min wedge happened because the inner sentinel poll trusted `timeout_s` (1800 s) and the outer code had no detection for "submission never landed." Add early-detection helpers (with their own short grace windows) at every place where we trust an asynchronous external event.
- **Pattern**: when changing a function signature from returning `None` to returning `bool`, callers that ignore the return value remain correct (Python doesn't enforce return-value usage). Backward-compatible API evolution.
