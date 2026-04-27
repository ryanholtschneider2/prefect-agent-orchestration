# Decision log — prefect-orchestration-3j8 (build iter 1)

- **Decision**: AC1 (data migration) verified by audit only; no migration code shipped.
  **Why**: Audited `.beads/metadata.json` shows `dolt_mode=server`, `dolt_database=prefect_orchestration`, host `127.0.0.1`. `.beads/dolt-server.{pid,port,log}` present. `bd list` returns issues. The migration was already performed before this build started (old data archived at `.beads/embeddeddolt.bak-20260424-163830/`). Plan §"Current state" + AC1 wording ("Current prefect-orchestration/.beads runs against dolt-server").
  **Alternatives considered**: Re-running the migration would be destructive and risk corrupting in-flight claims for this very run.

- **Decision**: Documented `bd init --server …` (not `bd init --backend=dolt-server`).
  **Why**: `bd init --help` shows the actual flag is `--server` (boolean) plus `--server-host/--server-port/--server-user/--database`. Triage flagged the issue's wording as needing verification. CLAUDE.md and README now show the verbatim invocation that `bd` accepts.
  **Alternatives considered**: Quoting the issue text verbatim would teach agents an invocation the CLI rejects.

- **Decision**: New check is `WARN` not `FAIL` on embedded-dolt; `OK` when no `.beads/` is present.
  **Why**: AC3 says "warns on embedded-dolt". `po doctor` runs in directories that aren't always rigs (e.g., dev shell anywhere); a missing `.beads/metadata.json` is not a problem to surface to humans.
  **Alternatives considered**: `FAIL` would gate `po doctor` exit-1 on solo rigs that haven't migrated, which breaks backwards compatibility for external users (issue's NOTES require warn-don't-break).

- **Decision**: Did not extend `tests/e2e/test_po_doctor_cli.py`.
  **Why**: Plan marked the e2e assertion as best-effort. The existing e2e mocks/intercepts core checks selectively; adding a new row would touch several harness fixtures for marginal coverage. Unit coverage in `tests/test_doctor_dolt.py` (5 cases — embedded, server, missing-mode, missing-meta, unreadable-json) is sufficient. Smoke-checked `po doctor` against the live rig: row renders OK with `dolt-server (db=prefect_orchestration, host=127.0.0.1)`.
  **Alternatives considered**: Extending the e2e harness — risked breaking unrelated parity epic work concurrently in flight.

- **Decision**: Did not run a live concurrency probe (4 background `bd update` shells).
  **Why**: Plan flagged risk of polluting the live tracker mid-run with another worker's edits in flight. Bench evidence: `bd list` and `bd show` returned promptly throughout this build with 17 other `po run` processes claiming/updating beads against the same server (per session memory_context). That's a more honest concurrency proof than a synthetic 4-shell probe.
  **Alternatives considered**: Synthetic probe in `tmp_path` against an isolated dolt-server — would be duplicative of what the real workload is already doing.
