# Decision log — prefect-orchestration-4h8.3

## Build iter 1

- **Decision**: In-flight guard uses a test seam (`_in_flight_probe`) rather than mocking `anyio.run` / `get_client` directly.
  **Why**: Keeps unit tests independent of Prefect client schemas and avoids `anyio.run` re-entrancy concerns. Mirrors the spirit of `status.find_runs_by_issue_id` being the non-Typer reusable seam.
  **Alternatives considered**: Monkeypatch `prefect.client.orchestration.get_client`; that would pull Prefect schema types into tests for no benefit.

- **Decision**: `_archive_run_dir` falls back to `shutil.move` on `OSError` from `Path.rename`.
  **Why**: Plan risks call out the cross-filesystem case. Cheap safety net; same-fs path stays atomic.
  **Alternatives considered**: Always `shutil.move` (loses atomic rename in the common case); hard-fail on cross-fs (brittle).

- **Decision**: `_bd_show_status` and `_bd_reopen` swallow non-zero bd exits and unparseable JSON silently (status becomes `None`, reopen is best-effort).
  **Why**: bd may be absent during a retry of a locally-tracked bead that was exported elsewhere. A bd hiccup shouldn't block a retry once metadata already resolved. The reopen side effect is a nicety, not a hard requirement of the flow contract.
  **Alternatives considered**: Raise on bd errors; would make the command fragile in offline / stale-CLI environments.

- **Decision**: Retained alphabetical import order in `cli.py` and left `_artifacts` / `_sessions` imports added by concurrent workers untouched.
  **Why**: Parallel-run hygiene rule in the prompt — other workers' in-flight work must not be clobbered.
  **Alternatives considered**: Revert to the pre-concurrent-import block seen in the plan-time read of `cli.py`. Rejected.

- **Decision**: Warn-but-proceed when `--keep-sessions` is set and no `metadata.json` exists.
  **Why**: Explicitly named in the plan's Risks section as an UX concern — silent no-op would surprise the user.
  **Alternatives considered**: Exit non-zero; would break the "convenient retry" ergonomic.

- **Decision**: Exit codes — 2 for RunDirNotFound (inherited), 3 for in-flight / lock contention, 4 for missing formula, 5 for flow raise.
  **Why**: Gives `beadsd` / scripts a way to distinguish "retriable" (3) from "broken" (2, 4) from "flow-level bug" (5). Mirrors `po logs`' exit-2 for missing metadata.
  **Alternatives considered**: Collapse all to 1; loses signal value.
