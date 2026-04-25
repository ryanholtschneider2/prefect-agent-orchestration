# Decision log — prefect-orchestration-4ja.6

- **Decision**: Added a `source: str = "core"` field to `CheckResult` rather than introducing a parallel renderer type for pack checks.
  **Why**: Lets the unified table render core + pack rows through one code path; `source` defaults to `"core"`, so existing call sites and tests keep working (additive change).
  **Alternatives considered**: A separate `PackCheckResult` dataclass with its own renderer (more code, more divergence); a `tag: dict` bag (looser typing).

- **Decision**: Two distinct dataclasses — public `DoctorCheck` (pack-facing) vs internal `CheckResult` (renderer-facing) — bridged by `_run_pack_check`.
  **Why**: Issue's design block names `DoctorCheck` with fields `name/status/message/hint` and string statuses `green|yellow|red`. Keeping that as the public surface decouples pack authors from the internal `Status` enum and `remediation` naming.
  **Alternatives considered**: One unified type. Rejected because pack authors shouldn't import `Status` to construct values.

- **Decision**: Per-check soft timeout via `concurrent.futures.ThreadPoolExecutor(max_workers=1).submit(...).result(timeout=...)`.
  **Why**: Plan called for it; cross-platform (no `signal.alarm`). Orphaned thread on hang is acceptable for a short-lived CLI per triage.
  **Alternatives considered**: `multiprocessing` (heavier, picklability issues for entry-point callables); `signal.alarm` (POSIX-only, breaks on Windows).

- **Decision**: Sort `po.doctor_checks` entry points by `(dist.name, ep.name)`.
  **Why**: Issue says "install order"; install order is unstable across pip/uv layouts. Alphabetical-by-pack is deterministic and gives users a predictable table.
  **Alternatives considered**: Iteration order (non-deterministic); registration order (no portable signal in `importlib.metadata`).

- **Decision**: Pack checks only run when `run_doctor()` is called with no explicit `checks` arg. Existing aggregator tests pass an explicit `checks` list, so they remain hermetic and don't accidentally pick up real pack checks.
  **Why**: Backwards compatibility for the existing test suite, which relies on `run_doctor([fn1, fn2])` returning exactly those rows.
  **Alternatives considered**: Always run pack checks. Rejected because it would require monkeypatching `_iter_doctor_check_eps` in every existing aggregator test.

- **Decision**: `_run_pack_check` swallows all failure modes (load error, timeout, exception, invalid status, wrong return type) and converts them to `CheckResult` rows rather than raising. Invalid status / wrong type → red FAIL.
  **Why**: The aggregator must never crash because one pack misbehaves; that's the whole point of the wrapper.
  **Alternatives considered**: Surfacing exceptions to the caller (would let one bad pack take down `po doctor` for everyone).

- **Decision**: Pack example check is `claude_cli_present` (probes `claude --version`) rather than `claude_cli_authenticated`.
  **Why**: Plan-critic nit #5 — `--version` doesn't actually verify auth. `claude_cli_present` is honest about what it checks; auth state is per-host and out of scope for a wiring smoke test.
  **Alternatives considered**: Probing `~/.claude/.credentials.json` existence (brittle to non-OAuth setups); a no-op stub (would not exercise the timeout path or subprocess wrapper).

- **Decision**: Render with a new `SOURCE` leftmost column (vs hiding pack provenance, or appending to the message).
  **Why**: Triage flagged provenance as important for debugging "which pack is broken?"; a column is greppable and aligns with the rest of the table.
  **Alternatives considered**: Suffix `(from <pack>)` in the message (uglier when wrapped); silently dropping source (loses triage signal).
