# Decision log — prefect-orchestration-8gc (build iter 1)

- **Decision**: `fetch_bead_metadata(issue_id)` lives in `attach.py` rather than reusing `BeadsStore`.
  **Why**: `BeadsStore` is `@dataclass(parent_id=...)` and methods raise `subprocess.CalledProcessError` on missing beads / no `bd`. The CLI attach path needs graceful "no metadata, fall through to local" semantics, which is cleaner as a free function that returns `{}` on every failure mode. Plan §6 also explicitly mentions reading from bead metadata in `sessions` — same helper serves both call sites.
  **Alternatives considered**: Subclass / wrap `BeadsStore` in a swallow-errors variant; pass through `BeadsStore` and try/except at call sites (would duplicate handling).

- **Decision**: `build_rows(run_dir, metadata, *, pod=None)` — pod injected via kwarg by the caller, not looked up inside `sessions.py`.
  **Why**: Keeps `sessions.py` formula-/bead-agnostic (it never imports `attach`). The CLI `sessions` command does the bead lookup once and threads the result in. Avoids a circular import (`attach.py` already imports `SESSION_PREFIX` from `sessions.py`).
  **Alternatives considered**: `sessions.build_rows` shells `bd show` directly (couples sessions to bd / attach); add a separate `build_rows_with_pod` helper (extra surface for a small change).

- **Decision**: `--print-argv` debug flag on `po attach`.
  **Why**: `os.execvp` requires a real TTY and would hijack pytest's stdout if invoked directly. The flag lets unit tests assert on the exact argv that *would* be exec'd without the TTY ceremony, and gives e2e harnesses a non-interactive way to validate against a kind cluster (plan §verification-strategy AC for kind e2e).
  **Alternatives considered**: Mock `os.execvp` only (works for unit, not e2e); add a separate `attach.dry-run` subcommand (more surface).

- **Decision**: `_session_name` on both `TmuxClaudeBackend` and the scoped `TmuxInteractiveClaudeBackend` delegates to `attach.session_name(issue, role)` via a local import.
  **Why**: Plan §risks calls out session-name churn as catastrophic. A single source of truth (attach.session_name) prevents the "TmuxClaudeBackend uses sanitized, attach uses raw" drift. The local import avoids a top-of-module circular dependency between `agent_session` and `attach`.
  **Alternatives considered**: Top-level import (would create cycle since `attach.py` imports from `sessions.py` which `agent_session` doesn't depend on, but kept defensive); duplicate the rule in three places (rejected — exactly the failure mode plan §risks names).

- **Decision**: Probe-pod check happens BEFORE `os.execvp`, but `--print-argv` skips both the probe and the exec.
  **Why**: Probing is a net-positive UX gate (clear "pod gone, run was on X — try `po retry`" vs cryptic kubectl exec failure). For `--print-argv` users (CI / debugging), the probe is an unwanted network call against an unreachable cluster — keep that path purely local.
  **Alternatives considered**: Always probe; never probe (bad UX on real attach).

- **Decision**: Skipped the `tests/e2e/test_attach_kind.py` AC for now — not implemented in this iter.
  **Why**: The plan's verification table accepts the unit-level argv assertion as the primary check; the kind-cluster e2e is gated behind `PO_E2E_KIND=1` and would need a kind cluster + image build harness this iter doesn't ship. Unit coverage covers the same logic surface (probe + argv builders + role disambig), and the CLI-level `tests/test_cli_attach.py::test_attach_prints_argv_kubectl` exercises end-to-end argv construction. A follow-up bead can add the gated e2e once a `PO_E2E_KIND` runner exists.
  **Alternatives considered**: Add a dummy skipped test scaffold (fake-green test pollution); block this iter on a kind-cluster harness (out of scope per plan §test-plan).
