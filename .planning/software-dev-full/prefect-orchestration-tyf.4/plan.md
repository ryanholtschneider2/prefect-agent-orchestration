# Plan — prefect-orchestration-tyf.4

Helm chart that packages the cloud deployment of PO: prefect-server,
po-worker (Deployment), pool-registration hook Job, rig PVC, OAuth
Secret stub, and an optional Prefect-UI Service+Ingress. Predecessors
tyf.1 (CLAUDE_CREDENTIALS env -> ~/.claude/.credentials.json) and
tyf.2 (`claude-context-overrides` ConfigMap) already shipped — chart
consumes them.

## Affected files

New (chart skeleton):

- `charts/po/Chart.yaml` — chart metadata, `appVersion`, no subcharts
- `charts/po/values.yaml` — knobs (replicaCount, pool, images, PVC,
  ingress, auth mode, prefect-server toggles)
- `charts/po/values.schema.json` — JSON-schema validation for values
- `charts/po/.helmignore`
- `charts/po/templates/_helpers.tpl` — `po.fullname`, `po.labels`,
  `po.workerImage`, `po.serverImage`, namespace helpers
- `charts/po/templates/serviceaccount.yaml` — `po-worker` SA + Role +
  RoleBinding (lifted from `k8s/po-worker-deployment.yaml`)
- `charts/po/templates/rig-pvc.yaml` — RWX/RWO toggle from values;
  default RWO + 20Gi (kind/minikube friendly)
- `charts/po/templates/prefect-server-deployment.yaml` — single-replica
  Deployment using `prefecthq/prefect:3-latest` (image+tag overridable)
- `charts/po/templates/prefect-server-service.yaml` — ClusterIP :4200
- `charts/po/templates/prefect-server-pvc.yaml` — Prefect SQLite/db
  PVC, conditional on `prefectServer.persistence.enabled`
- `charts/po/templates/prefect-server-ingress.yaml` — `if .Values.ingress.enabled`
- `charts/po/templates/po-worker-deployment.yaml` — derived from
  `k8s/po-worker-deployment.yaml`; image, replicaCount, pool, env,
  volumes (rig PVC + claude-context-overrides ConfigMap optional +
  OAuth claude-home PVC opt-in), auth mode (apikey vs oauth)
- `charts/po/templates/pool-register-job.yaml` — Helm pre-install /
  pre-upgrade Job, hook-delete-policy=before-hook-creation,
  hook-weight=-5; runs
  `prefect work-pool create <pool> --type process --overwrite || prefect work-pool create <pool> --type process` against `PREFECT_API_URL`
  inside the cluster (uses the worker image so `prefect` CLI is on PATH)
- `charts/po/templates/anthropic-api-key-secret.yaml` — created from
  values when `auth.mode=apikey` AND `auth.createSecret=true`; otherwise
  references existing secret name (default behavior — out-of-band create)
- `charts/po/templates/claude-oauth-secret.yaml` — same but for OAuth
- `charts/po/templates/claude-context-overrides-cm.yaml` — only renders
  when `claudeContextOverrides.create=true` (default false; ops use
  `scripts/sync-claude-context.sh` for the real overlay)
- `charts/po/templates/NOTES.txt` — post-install instructions
- `charts/po/templates/tests/test-pool-exists.yaml` — `helm test` Job
  that runs `prefect work-pool inspect <pool>` to verify the post-install
  state (smoke for AC #2)

Edited:

- `engdocs/work-pools.md` — new "## Helm install" section after
  "## Kubernetes" with `helm install po ./charts/po -n po --create-namespace`
  walkthrough, secret pre-create commands, ingress notes, and the
  `helm test po` smoke command. Add to top-of-file index.
- `README.md` — one-liner pointer to the new chart path under the
  existing k8s docs link.

## Approach

1. **Chart skeleton** — `helm create` then strip the example. Single
   chart, no subcharts. App `Chart.yaml` `appVersion` tracks core
   `pyproject.toml` version (manual sync — no remote yet, so no
   release automation needed).

2. **Reuse existing manifests verbatim where possible.** The
   `k8s/po-worker-deployment.yaml`, `k8s/po-rig-pvc.yaml`, and
   `k8s/po-base-job-template.json` are already battle-tested; the chart
   templates are 1:1 transliterations with `{{ .Values… }}` substitution
   instead of copy-paste editing. This keeps the imperative-`kubectl
   apply` path (still documented) and the `helm install` path drift-free.

3. **prefect-server in-chart vs upstream.** Triage flagged this. Decision:
   use upstream `prefecthq/prefect:3-latest` directly (matches
   `docker-compose.yml` line 15). No need to build a fork; the
   `agent-experiments/recurring/docker/prefect-server` reference in the
   issue description is just precedent, not a hard requirement. Knob
   `prefectServer.image.repository`/`.tag` lets ops swap if needed.
   Single replica, optional PVC for SQLite (default off → ephemeral
   pod, fine for kind/minikube smoke; doc that prod needs persistence).

4. **Pool registration race.** Per triage: pre-install/pre-upgrade Helm
   hook Job, runs `prefect work-pool create $POOL --type process`. Hook
   weight `-5` so the worker Deployment (no hook) waits until after
   hooks complete. Use the worker image so `prefect` CLI is available
   without a separate image dep. `--overwrite` not used because Prefect
   3 errors on duplicate; wrap in shell with idempotent fall-through.

5. **PVC defaults.** RWO + 20Gi by default (kind/minikube ship a
   `standard` storageclass that's RWO-only). RWX is a values knob with
   doc pointing at EFS/Filestore/NFS-CSI. Single-replica worker enforced
   when `rig.accessMode=ReadWriteOnce` via NOTES.txt warning + a
   schema rule (replicaCount=1 when accessMode=RWO).

6. **Auth (Secret).** Three modes:
   - `auth.mode=apikey` — env from existing `anthropic-api-key` Secret
     (default; mirrors current k8s docs).
   - `auth.mode=oauth` — env from existing `claude-oauth` Secret keyed
     `credentials.json` → `CLAUDE_CREDENTIALS`. Optionally mount the
     `po-claude-home` PVC at `/home/coder/.claude` for token-refresh
     persistence (tyf.3 opt-in).
   - `auth.createSecret=true` (off by default) — chart renders the
     Secret from values for fully-self-contained installs (e.g. `helm
     install --set auth.apiKey=…` for kind smoke). Default off so we
     don't tempt anyone to commit a real key.

7. **Ingress.** Off by default. When `ingress.enabled=true`, render a
   standard `networking.k8s.io/v1` Ingress for the prefect-server
   service. No cert-manager wiring — just `tls: {}` knob and a
   `ingressClassName` value.

8. **ConfigMap from tyf.2.** Chart references the `claude-context-overrides`
   ConfigMap as an `optional: true` volume (already done in the
   imperative manifest). Chart does NOT regenerate it — ops still run
   `scripts/sync-claude-context.sh --emit-configmap …` and `kubectl
   apply` separately. A `claudeContextOverrides.create=true` knob lets
   small inline overrides be set via values for one-shot tests, but the
   real overlay flow stays out-of-band.

9. **No CRDs, no subcharts, no chart registry.** This repo is local-only
   per CLAUDE.md; chart lives in-tree. `helm package` is doc'd but no
   `index.yaml` publishing.

## Acceptance criteria (verbatim from issue)

- `helm install po ./charts/po` works on a real cluster (kind/minikube
  ok for first pass; EKS/GKE for production-grade)
- Workers register with prefect-server, claim a flow run
- `engdocs/work-pools.md` updated with the helm path

## Verification strategy

- **AC #1 — `helm install` succeeds.** `helm lint charts/po` and
  `helm template charts/po | kubectl apply --dry-run=client -f -` in CI
  / locally; manual smoke against minikube documented in
  `engdocs/work-pools.md`. Builder will run `helm lint` + `helm
  template … | kubeval -` (or `kubectl apply --dry-run=server` if a
  cluster is up) to verify rendering.
- **AC #2 — workers register + claim a run.** Two-part check:
  1. `helm test po` runs the bundled `tests/test-pool-exists.yaml` Job:
     `prefect work-pool inspect $POOL` against the in-cluster
     prefect-server. Exit 0 ⇒ pool exists ⇒ pool-register hook fired
     successfully.
  2. Doc'd manual: `kubectl logs -f deployment/po-worker` shows
     `Worker '<id>' started!` and `Polling …`. `prefect deployment run
     <flow>/<dep> --param …` from a `client`-profile pod (or local
     `prefect` against a port-forwarded server) results in the worker
     picking up the run. This is the same loop the existing
     `k8s/po-worker-deployment.yaml` flow already supports — chart
     just packages it.
- **AC #3 — docs updated.** `engdocs/work-pools.md` gets a "## Helm
  install" section verified by grep in tests (see Test plan).

## Test plan

- **Unit (chart-level)** — `helm lint charts/po` and `helm template`
  smoke. Add a tiny pytest under `tests/test_helm_chart.py` that:
  1. Asserts `charts/po/Chart.yaml` exists and parses.
  2. Shells `helm lint charts/po` (skip if `helm` not on PATH).
  3. Shells `helm template charts/po --set ingress.enabled=true …`
     and asserts critical kinds appear (Deployment×2, Service×1+,
     Job(hook), Ingress, PVC×1+, SA, Role, RoleBinding) via simple
     YAML parsing of the multi-doc output.
  Skip-on-missing-helm so CI without helm doesn't break.
- **Doc check** — `tests/test_helm_chart.py::test_workpools_doc_mentions_helm`
  greps `engdocs/work-pools.md` for `helm install po ./charts/po` and
  `Helm install` section header. Cheap regression guard.
- **e2e** — out of scope for this bead; the actual cluster smoke
  (helm install + run a flow) is **bead `prefect-orchestration-tyf.5`**
  (this issue blocks it). Don't duplicate here.
- **playwright** — N/A (`has_ui: false`).

## Risks

- **No actual cluster in CI.** `helm template` + `helm lint` are the
  best we can do automatically; real `helm install` is manual and
  validated under tyf.5. Mitigated by `helm template | kubectl apply
  --dry-run=server` documented in the readme.
- **prefect-server image upstream changes.** `prefecthq/prefect:3-latest`
  is a moving target; pin via a values knob and document the tested
  tag in NOTES.txt. Not a breaking change in this bead — same image is
  already in `docker-compose.yml`.
- **Pool-register hook idempotency.** Re-installs / `helm upgrade`
  re-run the hook; Prefect's `work-pool create` errors on duplicate.
  Wrap in `||` to swallow "already exists" while still failing on
  network errors. (Validated by hook script `set -o pipefail; prefect
  work-pool inspect $POOL >/dev/null 2>&1 || prefect work-pool create
  $POOL --type process`.)
- **RWX vs RWO mismatch.** If user enables `replicaCount>1` with
  default RWO PVC, schedule will pin pods to one node or fail. Mitigated
  by `values.schema.json` rule + NOTES.txt warning. Default config is
  safe (1 replica, RWO).
- **Existing `k8s/*.yaml` drift.** Chart and imperative manifests must
  stay aligned. Builder will keep both — chart templates derive from
  the manifests, not replace them — and add a comment header to the
  imperative files pointing at the chart.
- **Secret handling.** Chart never embeds default API keys / OAuth
  blobs; defaults to "reference an existing Secret". Misconfiguration
  surfaces as worker pod CrashLoopBackoff with clear log line, not a
  silent default. NOTES.txt walks through the `kubectl create secret …`
  pre-step.
- **No git remote / no chart registry.** Chart lives in-tree only;
  installation path is `helm install po ./charts/po` (relative). Doc'd.
  Future bead can add OCI publish if/when this repo gets a remote.
- **API contract** — N/A. No core-Python module signatures change. No
  PO `register()` changes. No new `po` verb.
- **Migrations** — N/A (`needs_migration: false`).
