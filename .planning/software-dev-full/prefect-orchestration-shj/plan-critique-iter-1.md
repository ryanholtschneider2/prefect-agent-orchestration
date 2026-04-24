# Critique — plan iter 1 (`prefect-orchestration-shj`)

**Verdict: approved (with nits).**

## Fit
Plan addresses all 5 ACs directly: new `po.deployments` EP group, example in po-formulas pack for `epic-sr-8yu` nightly 09:00, `po deploy` lists and `--apply` creates, README docs, `po run` untouched.

## Scope
Appropriate. New module `deployments.py` keeps CLI thin; no gold-plating. Optional `--pack`/`--name`/`--work-pool` filters are cheap and natural.

## Approach
Grounded. Mirrors the real `_load_formulas` pattern at `cli.py:34-47` (including the pre-3.10 fallback). Uses Prefect 3's `flow.to_deployment` → `RunnerDeployment.apply()`, which is the correct upsert path. Correctly flags that `apply()` without a work pool yields a server-registered deployment with no executor — documented in Risks.

## AC testability
Each AC has a concrete verification (EP monkeypatch, editable install smoke, Typer `CliRunner`, README grep, regression on `po run`). AC 3's "creates them on the Prefect server" is split cleanly into a unit-level spy + manual smoke; reasonable given no Prefect server in CI.

## Nits (non-blocking)
- Risk correctly calls out that `Cron` import path may be `prefect.client.schemas.schedules` rather than `prefect.schedules`. Builder should resolve via context7 at implementation time — plan already says this.
- Verification table row for AC 5 references `--dry-run stub backend` which doesn't exist in current `run`; the baseline regression should just invoke a fake formula via CliRunner as the Test plan section already describes. Minor inconsistency; non-blocking.
- `register()` convention: plan says "may return a single deployment or a list; core normalizes". Worth adding to README that returning a generator also works, or explicitly restrict to list/single — small spec detail.

## Risks
Covered: Prefect API drift, work-pool requirement, eager `register()` side effects, idempotency, cross-repo edit in `../../software-dev/po-formulas`. No migrations, no contract changes for existing consumers.
