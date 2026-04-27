# Plan — prefect-orchestration-5wk.3

## CLAUDE_CREDENTIALS_POOL: multi-account credential routing in worker entrypoint

## Affected files

- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/docker/entrypoint.sh` — pool-aware credential pick, deterministic index from HOSTNAME, audit log of chosen index, scrub of pool envs before `exec`. Also add the analogous `ANTHROPIC_API_KEY_POOL` path. Single-env precedence retained.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/charts/po/values.yaml` — add `auth.oauth.pool` and `auth.apikey.pool` blocks (`enabled`, `size`, `credentials` list, `apiKeys` list, `existingSecret`, `secretKey`).
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/charts/po/values.schema.json` — schema for the new pool sub-objects + assert `len(credentials) == size` when `createSecret=true`.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/charts/po/templates/claude-oauth-secret.yaml` — when `auth.oauth.pool.enabled` and `createSecret`, render a `claude-oauth-pool` Secret with key `pool` holding a JSON array.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/charts/po/templates/anthropic-api-key-secret.yaml` — analogous `anthropic-api-key-pool` Secret with key `pool` (JSON array).
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/charts/po/templates/po-worker-deployment.yaml` — when pool is enabled, project the `pool` Secret key as `CLAUDE_CREDENTIALS_POOL` / `ANTHROPIC_API_KEY_POOL` env (via `secretKeyRef`); otherwise current single-key path. Also wire `HOSTNAME` (already present from k8s downward API by default but make explicit via `valueFrom: fieldRef.metadata.name` so StatefulSet/Deployment ordinals are deterministic).
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_docker_entrypoint.py` — unit-level tests (subprocess against bash, tmp HOME) for: pool→deterministic index, `_INDEX` override, single-env precedence, malformed JSON falls through to apikey/error, log line includes `pool=` and index but not credential bodies.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_helm_chart.py` — `helm template` assertions: pool Secret rendered when enabled, env wiring on Deployment, schema validation of pool size.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/engdocs/auth.md` — append "Multi-account pool" section: schema, hash semantics, OAuth-vs-API-key tradeoff, kubectl recipe for out-of-band Secret creation.

No changes to core Python (`prefect_orchestration/`) — entrypoint is shell, and the auth picker runs entirely inside the container.

## Approach

### Entrypoint logic (`docker/entrypoint.sh`)

Insert a new "pool resolution" stanza **before** the existing OAuth/apikey precedence block, gated on the single-env vars being unset (single-env wins by spec):

```
if [[ -z "${CLAUDE_CREDENTIALS:-}" && -n "${CLAUDE_CREDENTIALS_POOL:-}" ]]; then
  pool_size = jq 'length' on $CLAUDE_CREDENTIALS_POOL
  if PO_CREDENTIALS_POOL_INDEX set, idx = that
  else idx = $(printf '%s' "$HOSTNAME" | sha256sum | cut -c1-8 | hex→dec) % pool_size
  CLAUDE_CREDENTIALS = jq -c ".[$idx]" of pool
  PO_AUTH_POOL_INDEX = idx
  unset CLAUDE_CREDENTIALS_POOL
fi
```

Mirror block for `ANTHROPIC_API_KEY_POOL` → `ANTHROPIC_API_KEY` (string element rather than object). Same `(_POOL && !single)` precedence and same `_INDEX` override knob.

Implementation notes:

- **Hash function**: portable, uses only tools already in the image (`sha256sum`, `cut`, `printf`, `bc` or `$(( 16#… ))`). Take first 8 hex chars (32-bit), bitwise-mod by `pool_size` via `$((16#$hex % size))`. Avoids `cksum` which is non-portable on alpine.
- **StatefulSet ordinal optimization** (addresses triage risk on uniformity): if `HOSTNAME` matches `^.*-[0-9]+$`, use the trailing integer mod `pool_size` instead of hash. Guarantees perfectly even spread for `replicas % pool_size == 0`. Falls back to sha256-mod for non-ordinal hostnames (Deployments). Document this branch in auth.md so operators know to size replicas as a multiple of pool size.
- **Single-env precedence**: keep the existing `[[ -n "${CLAUDE_CREDENTIALS:-}" ]]` branch unchanged; just add the pool resolution before it that *populates* `CLAUDE_CREDENTIALS` from the pool when the single env is missing. This preserves the current code path (and its tests) and makes the pool a transparent expansion.
- **Audit log**: emit `po-entrypoint: auth=oauth source=pool index=<idx> size=<n>` when pool used. Never echo the credential body. Do not enable `set -x` (existing guardrail test enforces this).
- **Scrub `CLAUDE_CREDENTIALS_POOL` and `ANTHROPIC_API_KEY_POOL` from environ** before `exec` (alongside the existing `unset` calls) so child processes / `/proc/<pid>/environ` don't leak the array.
- **Malformed JSON**: if `jq` exits non-zero on the pool, log `error: invalid CLAUDE_CREDENTIALS_POOL JSON` and exit 64. Don't silently fall through to apikey — that hides config bugs.
- **`jq` requirement**: confirm `jq` is in the `Dockerfile` base image; if not, add to apt install layer. (Cheap dep, already commonly present.) Check with `find Dockerfile -exec grep jq {} +` during build.

### Chart wiring

`values.yaml` additions under `auth`:

```yaml
auth:
  apikey:
    pool:
      enabled: false
      size: 0                    # asserted == len(apiKeys) when createSecret
      existingSecret: ""         # operator-provided
      secretKey: pool
      createSecret: false
      apiKeys: []                # list[string], only honored when createSecret
  oauth:
    pool:
      enabled: false
      size: 0
      existingSecret: ""
      secretKey: pool
      createSecret: false
      credentials: []            # list[object]; rendered as JSON array
```

When `pool.enabled`, the worker Deployment swaps the `CLAUDE_CREDENTIALS` / `ANTHROPIC_API_KEY` env name for the `_POOL` variant, sourcing from the configured Secret. Mutually exclusive with the corresponding single-key block — document via Helm `fail` if both `createSecret`s are true. Stick to the **single-key-holds-JSON-array** strategy (triage option B) to avoid chart loops and keep entrypoint logic identical to the env-var spec; indexed-keys is a future option if GitOps diff readability becomes a pain point.

`HOSTNAME` is already exported automatically by the kubelet for every container — no need to add a downward-API env. (Verify in test by `helm template … | yq` and a runtime `kubectl exec` smoke not required for unit/e2e here.)

### Doc update

`engdocs/auth.md` — append a new section `## Multi-account pool` with:

1. When to use it (rate-limit fanout > single account).
2. Schema and precedence (single-env wins).
3. Hash semantics + ordinal-hostname fast path.
4. OAuth vs API-key tradeoffs (sub-billing vs higher RPS).
5. Out-of-band Secret recipe:
   ```
   kubectl create secret generic claude-oauth-pool \
     --from-file=pool=<(jq -s . cred1.json cred2.json … credN.json)
   ```
6. `_INDEX` test override.
7. Caveats: hash uniformity for non-ordinal hostnames; scaling replicas as a multiple of `poolSize`.

## Acceptance criteria (verbatim)

- Worker entrypoint logs which credentials index it picked at boot.
- 100 replicas with poolSize=10 spread evenly (each account gets ~10 replicas).
- Single-account mode (CLAUDE_CREDENTIALS) still works unchanged.
- Tests: unit (entrypoint shell logic via bats or python wrapper), e2e (kind cluster with 5 replicas + 2-account pool, verify 2-3 replicas land on each account).
- engdocs/auth.md updated with the pool recipe.

Plus the bd `acceptance` field: "Pool routing works deterministically; single-account fallback preserved; chart wiring; tests; doc".

## Verification strategy

| AC | Concrete check |
|---|---|
| Logs index at boot | `tests/test_docker_entrypoint.py::test_pool_logs_index` — assert stderr contains `index=<int>` and `size=<n>`, never the credential body. |
| 100 replicas spread evenly | Unit: simulate 100 hostnames `worker-0` … `worker-99` with `poolSize=10`; assert each index 0–9 picked exactly 10 times (ordinal fast path makes this exact). Plus a sha256 fallback test on 100 random hostnames asserting every bucket is hit and max bucket ≤ 2× ideal (chi-squared softness for the non-ordinal path). |
| Single-account unchanged | Existing `test_oauth_mode_materializes_credentials` + new test that sets *both* `CLAUDE_CREDENTIALS` and `CLAUDE_CREDENTIALS_POOL` and asserts the single env wins (pool ignored, scrubbed). |
| Unit tests for pool logic | Python subprocess tests in `tests/test_docker_entrypoint.py` (no bats — Python is already the convention here, `bash -n` syntax check already passes). |
| e2e (kind + 5 replicas + 2 accounts) | Add `tests/e2e/test_pool_routing_kind.py` **only if a kind cluster is part of the existing e2e fixture set**; otherwise emit a **stub-mode e2e**: run the entrypoint container 10× with HOSTNAME=`worker-N` and `PO_BACKEND=stub`, capture each container's `~/.claude/.credentials.json`, assert distribution. Skipped (with reason) if `docker` not on PATH. Heavyweight kind+real-account e2e is deferred to manual smoke per the cost concern in triage.md. |
| Helm chart wiring | `tests/test_helm_chart.py` — `helm template charts/po --set auth.oauth.pool.enabled=true --set auth.oauth.pool.existingSecret=foo` and assert: (a) Deployment env contains `CLAUDE_CREDENTIALS_POOL` from `secretKeyRef.name=foo,key=pool`, (b) no `CLAUDE_CREDENTIALS` env, (c) `helm lint` passes, (d) values.schema.json validates pool block. |
| Doc updated | `tests/test_helm_chart.py::test_auth_md_documents_pool` — grep `engdocs/auth.md` for `CLAUDE_CREDENTIALS_POOL` and `ANTHROPIC_API_KEY_POOL` headings; cheap drift guard. |

## Test plan

- **unit** (`tests/test_docker_entrypoint.py`, `tests/test_helm_chart.py`):
  - pool resolves index from HOSTNAME (sha256 path).
  - pool resolves index from ordinal hostname (`worker-7` → 7 % size).
  - `PO_CREDENTIALS_POOL_INDEX` override beats hash.
  - `CLAUDE_CREDENTIALS` (single) beats `CLAUDE_CREDENTIALS_POOL`.
  - Same matrix for `ANTHROPIC_API_KEY_POOL`.
  - Malformed pool JSON exits 64 with clear error.
  - Audit log line never contains pool body (regex check).
  - 100-hostname distribution test (ordinal path: exact even; hash path: max bucket ≤ 2× expected).
  - `helm template` env+secret rendering for both pool variants.
  - values.schema.json validates pool block.
- **e2e** (`tests/e2e/test_pool_routing_*.py`):
  - Stub-mode docker-compose: spin up 10 replicas with hostname overrides; assert each gets a distinct (or evenly-distributed) index.
  - Full kind cluster smoke: documented manually in `engdocs/auth.md` ("validation" subsection); not run in CI by default. The bd acceptance demands it but the triage risk on cost is real — I'll include a `@pytest.mark.kind` marked test that's skip-by-default and runnable on demand (`pytest -m kind`).
- **playwright**: N/A (no UI).

## Risks

- **Hash uniformity for non-ordinal hostnames** (Deployment-style random-suffix names like `po-worker-7d4f9-abcde`): sha256-mod is only *statistically* even. For `replicas=100, poolSize=10`, expected stdev ≈ 3, so a worst-case bucket might see 13–14 replicas. The ordinal fast path mitigates this when the chart is deployed as a StatefulSet (or single-replica Deployment scaled with predictable names); document the caveat. If perfectly even spread is required, operators can pin `PO_CREDENTIALS_POOL_INDEX` per replica via a StatefulSet-only env trick, but that's beyond this issue's scope.
- **`jq` dependency**: if not in the worker base image, build size grows by ~1 MB. Acceptable; alternative (pure-bash JSON array slicing) is fragile and not worth it.
- **Secret payload size**: a 100-account pool of OAuth blobs (~2 KB each) ≈ 200 KB; well under the 1 MiB Secret limit, but document.
- **Backwards compatibility**: existing `CLAUDE_CREDENTIALS` / `ANTHROPIC_API_KEY` paths must remain pixel-identical. The plan adds pool resolution as a *prefix* that only activates when both single envs are absent — this preserves all current entrypoint tests unchanged.
- **No API contract change**: pool envs are container-internal; no PO Python API surface, no flow signature, no Prefect deployment param touched. Risk of breaking downstream consumers is zero.
- **Log scraping**: any log aggregator alerting on `auth=oauth` lines will see a new `pool index=` suffix — minor format change. Document in auth.md.
- **`set -x` regression**: the new pool block uses `jq` and arithmetic; ensure no debug `set -x` is added during development. Existing `test_entrypoint_does_not_set_x` guard catches this.
- **Migration**: none. New fields are opt-in; default `pool.enabled=false` keeps existing deployments unchanged.
