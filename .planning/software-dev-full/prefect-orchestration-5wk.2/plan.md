# Plan: prefect-orchestration-5wk.2 — HPA + 100-concurrency on po-worker for fanout demos

## Approach

Add a HorizontalPodAutoscaler template targeting the `po-worker` Deployment, expose
its knobs in `values.yaml` + `values.schema.json`, ship a `values-demo.yaml` profile
that bumps min/max replicas to 20/100 and pool concurrencyLimit to 100, and extend
the existing pre-install pool-register Job to apply a `--concurrency-limit` derived
from values. Add helm-template unit tests in `tests/test_helm_chart.py` (the
existing pytest-driven harness — no new tooling) and document the demo profile in
`engdocs/work-pools.md`.

Key implementation notes grounded in current code:

- HPA targets `kind: Deployment, name: {{ include "po.workerName" . }}` — same name
  used in `po-worker-deployment.yaml:5`. `apiVersion: autoscaling/v2`.
- Default `worker.autoscaling`: `enabled: false` (preserve current single-replica
  RWO default). When `enabled: true`, render HPA and **omit** `spec.replicas` from
  the worker Deployment (HPA-managed replicas conflict with a static replicaCount
  on each helm upgrade). Conditional in `po-worker-deployment.yaml`.
- Pool concurrency: extend `pool-register-job.yaml` script to call
  `prefect work-pool set-concurrency-limit "$POOL_NAME" "$POOL_CONCURRENCY"` after
  the create/inspect branch (idempotent — runs every pre-install/pre-upgrade).
  `POOL_CONCURRENCY` env from `.Values.pool.concurrencyLimit` (default 5; demo 100).
  When the value is `null`/`0`, skip the call (leave pool unbounded).
- `values-demo.yaml` overrides only the demo-specific deltas (autoscaling on with
  min=20/max=100, `pool.concurrencyLimit=100`); keep everything else inheriting
  defaults so it composes with existing profiles.
- Schema: add `pool.concurrencyLimit` (`integer`, `minimum: 0`) and
  `worker.autoscaling` (object with `enabled`, `minReplicas`, `maxReplicas`,
  `targetCPUUtilizationPercentage`), all optional, `additionalProperties: true` is
  already set so existing values files keep validating.

## Affected files

- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/charts/po/values.yaml` — add `pool.concurrencyLimit`, `worker.autoscaling` block.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/charts/po/values.schema.json` — schema for the new keys.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/charts/po/values-demo.yaml` — **new**; demo profile.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/charts/po/templates/po-worker-hpa.yaml` — **new**; HPA template gated on `worker.autoscaling.enabled`.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/charts/po/templates/po-worker-deployment.yaml` — make `spec.replicas` conditional on `not .Values.worker.autoscaling.enabled`.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/charts/po/templates/pool-register-job.yaml` — add `POOL_CONCURRENCY` env + idempotent set-concurrency-limit call.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_helm_chart.py` — add tests asserting HPA renders with demo profile, replicas omitted when autoscaling on, pool job env carries concurrency, demo overrides parse.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/engdocs/work-pools.md` — section "Demo profile (large fanout)" documenting `-f charts/po/values-demo.yaml` and node-pool sizing assumption.

## Acceptance criteria (verbatim from issue)

- `helm upgrade --install po ./charts/po -f charts/po/values-demo.yaml` applies HPA with maxReplicas=100, work pool with concurrency=100.
- Test: `kubectl get hpa -n po` shows the resource; chart unit tests verify rendered manifest fields.
- `engdocs/work-pools.md` or new `engdocs/scaling.md` documents the demo profile knob.

## Verification strategy

| AC | Concrete check |
|---|---|
| Demo profile renders HPA(maxReplicas=100) + concurrency=100 | New pytest invokes `helm template po ./charts/po -f charts/po/values-demo.yaml`, parses YAML, asserts a `HorizontalPodAutoscaler` doc exists with `spec.maxReplicas == 100`, `spec.minReplicas == 20`, `spec.scaleTargetRef.kind == "Deployment"`, name matches po-worker. Asserts pool-register Job env contains `POOL_CONCURRENCY=100`. |
| Default profile **does not** render HPA | New pytest asserts no `HorizontalPodAutoscaler` kind in default `helm template` output and worker Deployment has `spec.replicas: 1`. |
| Concurrency env wired through | Pytest greps rendered Job script for `set-concurrency-limit` and `POOL_CONCURRENCY`. |
| `kubectl get hpa` works | Manual smoke (recorded in PR description) — `helm upgrade --install ... -f values-demo.yaml` against minikube/kind, then `kubectl get hpa`. Not automated in this bead (no live cluster in unit layer). |
| Doc updated | Pytest asserts `engdocs/work-pools.md` contains the literal `values-demo.yaml` reference and "demo profile" section header. |
| `helm lint` clean for both default and demo | Existing `test_helm_lint_clean` still passes; add a parameterized variant covering `-f values-demo.yaml`. |

## Test plan

- **unit** (`tests/test_helm_chart.py`) — all of the above; runs `helm` subprocess, skips when `helm` is missing on PATH (existing pattern at `_have_helm()`).
- **e2e** — none (skipped per repo `.po-env` `PO_SKIP_E2E=1`; chart smoke is unit-layer here).
- **playwright** — n/a (no UI).

## Risks

- `spec.replicas` removal under HPA: must guard with `{{- if not .Values.worker.autoscaling.enabled }}` so existing deployments without HPA are unaffected. Verified by default-profile test asserting `replicas: 1` still present.
- Pre-install Job idempotency: `prefect work-pool set-concurrency-limit` must be safe to re-run on every upgrade. The CLI is idempotent; if it fails on missing pool we already created the pool earlier in the same script, so order matters — set-concurrency-limit AFTER create/inspect. No state to migrate.
- Schema backward compat: new keys are optional; existing values files (`values-staging.yaml`, etc., if any) keep validating because they don't reference the new keys.
- Node capacity for demo profile: documented assumption only — chart can't enforce. Add a NOTES.txt warning if demo profile is detected (nice-to-have, not required by AC).
- API version `autoscaling/v2` is GA on k8s 1.23+; minikube/kind ship 1.27+, EKS supports it. No fallback needed.
- Out of scope (filed as follow-up bead suggestion in the PR description, per triage): external-metric autoscaling on Prefect work-queue depth.
