# Triage: prefect-orchestration-shj

## Summary
Add a `po deploy` CLI verb to the `prefect-orchestration` (po) tool that discovers `register()` entry points from installed formula packs via the `po.deployments` entry-point group. Each pack declares its own Prefect deployments (cron/interval/manual) using Prefect-native APIs (`flow.serve` / `flow.deploy`). The verb should list registered deployments and, with `--apply`, create them on the Prefect server. Includes an example in the `po-formulas` pack (nightly 09:00 run for epic-sr-8yu) and README documentation of the `register()` convention. Existing `po run` must continue to work unchanged. This is a Prefect-native replacement for the previous Gas City "orders" concept.

## Flags
- `has_ui`: **false** — CLI-only; event trigger UI is delegated to Prefect's own UI (Automations).
- `has_backend`: **true** — new CLI verb, entry-point discovery, Prefect deployment API calls.
- `needs_migration`: **false** — no schema/DB changes; deployments live in Prefect server.
- `is_docs_only`: **false** — code + docs.

## Risks / Open Questions
- Entry-point discovery semantics: single-process (`importlib.metadata.entry_points(group="po.deployments")`) — confirm packs are installed in the active venv.
- `flow.serve` vs `flow.deploy`: which is used for `--apply`? `serve` runs a long-lived process; `deploy` registers against a work pool. The AC says "creates them on the Prefect server" — implies `deploy`, which requires a work pool / worker infrastructure to be configured.
- Cooldown semantics with Prefect intervals: need to confirm "cooldown" maps cleanly to Prefect's interval schedule (no native cooldown-since-last-success concept).
- Manual-trigger deployments: Prefect allows deployments with no schedule — fine, but `--apply` behavior for these should be explicit (create but don't schedule).
- `po-formulas` pack: does it exist yet in this repo, or is it a separate package? Affects whether the example lives here or requires a second repo change.
- Idempotency of `--apply`: re-running should upsert, not duplicate deployments.
- Backward compat guarantee for `po run` — need a smoke test, not just an assertion.
