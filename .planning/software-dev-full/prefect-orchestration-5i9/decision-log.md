# Decision log — prefect-orchestration-5i9 (build iter 1)

- **Decision**: Python tail-N loop instead of `subprocess.run(["tail", ...])` for the default (non-follow) path.
  **Why**: Plan specifies "no new deps" and testability — a pure-Python reader is trivial to unit-test with a seeded tmp_path; `os.execvp("tail", …)` is still used for `-f` because signal handling on a streaming follow is where `tail(1)` actually earns its keep.
  **Alternatives considered**: shelling out to `tail -n` for both paths (simpler but makes stdout capture in tests flakier and re-shells for every invocation).

- **Decision**: Pack flow writes metadata via a direct `subprocess.run(["bd", "update", ..., "--set-metadata", ...], check=False)` rather than going through `BeadsStore.set`.
  **Why**: The two writes share a single `bd update` invocation — atomic from the user's POV, and avoids importing/constructing a throwaway `BeadsStore` when `parent_bead` may be None. Guarded with `shutil.which("bd")` and `dry_run` per critic nit #1.
  **Alternatives considered**: `BeadsStore(parent_id=issue_id).set(...)` twice — two subprocesses, no atomicity gain.

- **Decision**: Left sibling worker's additions (`prefect_orchestration/status.py`, `doctor.py`, and the `from prefect_orchestration import status as _status` import they added to `cli.py`) in place without touching them.
  **Why**: Parallel-run hygiene rule #3 — those files belong to another worker's in-flight issue and are not on this plan's "Affected files" list.
  **Alternatives considered**: Reverting the import to keep my cli.py diff minimal — rejected because reverting another worker's change mid-flight creates merge conflicts downstream.

- **Decision**: `RunLocation` is a frozen dataclass returned by `resolve_run_dir`, not a bare tuple.
  **Why**: Named access (`loc.run_dir`) reads better in the sibling verbs that are about to consume it (`8bd/cdu/qrv/zrk`). Frozen so it can be used as a map key if a verb needs to memoize per-run state.
  **Alternatives considered**: `tuple[Path, Path]` (loses names); `TypedDict` (mutable, more ceremony).

- **Decision**: `candidate_log_files` filters Prefect `/tmp/prefect-orchestration-runs/*.log` by `mtime >= run_dir.mtime` rather than reading a flow-run id from metadata.
  **Why**: The flow does not currently persist its flow-run id on the bead; doing so is a scope creep that belongs to `cdu` (po sessions). mtime heuristic is correct for the common case (one concurrent run per issue) and is called out in plan risks.
  **Alternatives considered**: Adding `po.flow_run_id` metadata write now — rejected, defer to sessions verb.

- **Decision**: Unit-test strategy for `-f/--follow` is to monkey-patch `cli.os.execvp` and assert argv shape.
  **Why**: Actually streaming is a moving target without a fake clock; the argv shape is the observable contract. Matches plan AC3 verification.
  **Alternatives considered**: Running real `tail -F` against a tmp file with a background writer (flaky, slow, CI-hostile).
