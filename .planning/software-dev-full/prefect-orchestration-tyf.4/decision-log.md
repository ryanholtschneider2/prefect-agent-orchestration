# Decision log — prefect-orchestration-tyf.4 (builder iter-1)

- **Decision**: Use upstream `prefecthq/prefect:3-latest` as the
  prefect-server image, exposed as values knobs (`prefectServer.image.repository|tag`).
  **Why**: Triage flagged the question. Same image already runs in
  `docker-compose.yml`; bundling a fork from `agent-experiments/...`
  introduces a maintenance burden with no current upside.
  **Alternatives considered**: Build a forked image inside this repo
  (rejected — no value-add); pin to a specific minor (rejected for
  v0.1.0; documented in NOTES.txt that prod should pin).

- **Decision**: Pool registration via Helm `pre-install,pre-upgrade`
  hook Job (weight `-5`) using the worker image, idempotent shell
  script (`prefect work-pool inspect || prefect work-pool create`).
  **Why**: Plan §4. Eliminates the worker-vs-pool race; reuses worker
  image so no extra image dep; idempotent shell handles repeat installs
  cleanly (Prefect 3 errors on duplicate create).
  **Alternatives considered**: init container on the worker (rejected —
  fires every replica, no central retry); standalone Deployment
  (rejected — pool-create is one-shot).

- **Decision**: Default `rig.accessMode=ReadWriteOnce`, 20 GiB, 1
  worker replica.
  **Why**: Plan §5; kind/minikube ship RWO-only `standard`
  storageClass. RWX is opt-in and documented in `engdocs/work-pools.md`
  + NOTES.txt warns when `replicaCount>1` + RWO is set.
  **Alternatives considered**: Default RWX (rejected — breaks the
  smoke path on kind/minikube and tyf.5 wants those to work first).

- **Decision**: Auth Secrets are referenced by name by default
  (`createSecret=false`); chart only renders them when an operator
  opts in via values + supplies the value, with a `fail` template
  guard if value is blank.
  **Why**: Plan §6. Prevents the chart values from becoming a
  vector for committed plaintext credentials, while still letting
  kind/minikube smokes set `--set auth.apikey.createSecret=true
  --set auth.apikey.apiKey=…` for fully-self-contained installs.
  **Alternatives considered**: Always render the Secret from values
  (rejected — too easy to commit a real key); never render the
  Secret (rejected — friction for ephemeral test installs).

- **Decision**: `claude-context-overrides` ConfigMap referenced as an
  `optional: true` volume; chart does NOT regenerate it from the
  host's `~/.claude` (that path stays in `scripts/sync-claude-context.sh`).
  **Why**: Plan §8. The real overlay flow ships ~MiB of files via a
  dedicated script that emits a ConfigMap manifest; embedding that
  in the chart would either require the script as a Helm
  pre-install hook (complex, not worth it) or duplicate the
  flattening logic in templates (rotting copy).
  **Alternatives considered**: Auto-generate ConfigMap during chart
  install (rejected — rotting copy of `sync-claude-context.sh`).

- **Decision**: Skip a separate `Dockerfile` for the pool-register
  Job; reuse the worker image (Prefect CLI is already on PATH).
  **Why**: Plan §4. Worker image already has `prefect`. Saves a
  build/push step and eliminates a class of "two images drift".
  **Alternatives considered**: Tiny standalone image with
  `prefecthq/prefect` (rejected — extra image, same outcome).

- **Decision**: `helm test` Pod uses `prefect work-pool inspect`,
  not `prefect deployment run`.
  **Why**: AC #2 splits into "pool exists" (chart's responsibility)
  and "worker can claim a flow run" (deployment + smoke test).
  Running an actual flow is tyf.5's job; the chart's `helm test`
  asserts only what the chart itself created.
  **Alternatives considered**: Trigger a real software-dev-full run
  (rejected — couples chart tests to a specific pack and to
  Anthropic API availability).

- **Decision**: Tests use `helm template` and parse multi-doc YAML
  with `pyyaml`; skip cleanly when `helm` is not on PATH.
  **Why**: Plan "Test plan / Unit". CI may or may not have helm; we
  want the suite to pass either way. Parsing kind counts is a tight
  regression guard against accidental template breakage.
  **Alternatives considered**: Shell out to `kubeval` (rejected —
  extra binary dep); only parse stdout text (rejected — fragile).

- **Decision**: ServiceAccount/Role/RoleBinding rendered even when
  `pool.type=process` (the default), gated only by
  `worker.serviceAccount.create`.
  **Why**: Cheap to keep, and the chart is meant to be reusable for
  `pool.type=kubernetes` clusters where the SA is required. Avoids
  ConfigMap-style "set this knob and that knob" combos.
  **Alternatives considered**: Gate SA creation on `pool.type=kubernetes`
  (rejected — surprising when the user later switches to a k8s pool
  and gets RBAC errors).
