# Decision Log — prefect-orchestration-dmy

- **Decision**: Use Prefect's native `tags` (stringly-typed) with convention `issue_id:<id>` rather than a new label system.
  **Why**: Prefect 3 `FlowRunFilterTags.all_` filters server-side; no schema change needed; matches plan §Approach.
  **Alternatives considered**: Custom labels via `empirical_policy`, flow-run name mangling — both unfilterable server-side or ugly.

- **Decision**: Mutate tags via the **sync** Prefect client (`get_client(sync_client=True)`) inside flow bodies.
  **Why**: Flow bodies are sync-stack; `anyio.run` from inside a Prefect-managed worker risks nested-loop errors on Prefect 3.6. Sync client sidesteps all event-loop shenanigans. Plan §Risks called out "API drift" — this is the safest path.
  **Alternatives considered**: (1) `prefect.runtime.flow_run.add_tags` — does not exist in installed 3.6.27 (only `.tags` read attribute); (2) async `get_client()` wrapped in `anyio.run` — works in the bare-thread case but breaks if the flow is invoked from an async caller.

- **Decision**: Error handling in `po status` catches **all exceptions**, prints a one-line `error:` to stderr, and exits 0.
  **Why**: AC3 ("exits 0 always — observation, not check") explicitly requires it; plan §Risks flagged this as unusual but deliberate.
  **Alternatives considered**: Exit non-zero on connect errors — matches normal CLI hygiene but violates the AC.

- **Decision**: Default `--since` window is `24h`; `--all` overrides it.
  **Why**: Without a window, the first invocation against a long-lived Prefect server pulls every run ever. 24h is a pragmatic default; `--all` keeps the escape hatch. Plan §Design Decisions.
  **Alternatives considered**: 7d default (too much), no default (dumps whole history — slow), env var — overkill for one knob.

- **Decision**: Client-side filter for untagged runs when no `--issue-id` is provided (no server-side "tag-prefix" filter available in Prefect 3.6).
  **Why**: `FlowRunFilterTags.all_` requires an exact tag string; Prefect has no `startswith`. Pulling `limit=200` and filtering locally is fine for single-user Prefect. Plan §Risks.
  **Alternatives considered**: Two server roundtrips (list tags, then filter) — not supported by client API; custom PostgREST — out of scope.

- **Decision**: Concurrent `cli.py` edits (`logs`, `doctor`) from other workers were already present. I added `status` at the bottom of the file and appended `_status` to imports without touching any sibling code.
  **Why**: Parallel-run hygiene — don't sweep up another worker's work.

- **Decision**: Tag `epic_run` with **both** `issue_id:<epic>` and `epic_id:<epic>`.
  **Why**: `issue_id:<epic>` keeps `po status` (which groups by `issue_id:`) showing the epic row too; `epic_id:<epic>` lets future `po status --epic` or child-to-epic joins disambiguate. AC2 says "one row per issue" — an epic bead *is* an issue.
  **Alternatives considered**: Only `epic_id:`; would hide epic runs from default `po status` output.
