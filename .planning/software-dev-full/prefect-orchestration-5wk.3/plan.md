# Plan — prefect-orchestration-5wk.3

## CLAUDE_CREDENTIALS_POOL: multi-account credential routing in worker entrypoint

## Affected files

- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/docker/entrypoint.sh` — pool-aware credential pick, deterministic index from HOSTNAME, audit log of chosen index, scrub of pool envs before `exec`. Add the analogous `ANTHROPIC_API_KEY_POOL` path. Single-env precedence retained.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/charts/po/values.yaml` — add `auth.oauth.pool` and `auth.apikey.pool` blocks (`enabled`, `size`, `secretName`, `secretKey`, `createSecret`, `credentials` / `apiKeys`).
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/charts/po/values.schema.json` — schema for the new pool sub-objects.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/charts/po/templates/claude-oauth-secret.yaml` — when `auth.oauth.pool.enabled && createSecret`, render a `claude-oauth-pool` Secret with key `pool` holding a JSON array; assert size matches `len(credentials)`.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/charts/po/templates/anthropic-api-key-secret.yaml` — analogous `anthropic-api-key-pool` Secret. Single-key path also gated on `not pool.enabled` so the chart renders exactly one Secret per mode.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/charts/po/templates/po-worker-deployment.yaml` — when pool is enabled, project the Secret as `CLAUDE_CREDENTIALS_POOL` / `ANTHROPIC_API_KEY_POOL` env (via `secretKeyRef`); otherwise current single-key path.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_docker_entrypoint.py` — unit tests via subprocess against bash with tmp HOME: pool→deterministic index, ordinal fast path, `_INDEX` override, single-env precedence, malformed JSON exits 64, audit log includes `pool index=` but never the credential bodies, 100-replica distribution check (perfect for ordinal, ≤2× expected for hash fallback).
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_helm_chart.py` — `helm template` assertions: pool Secret rendered when enabled, env wiring on Deployment, size-mismatch fail, doc-mention guard.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/engdocs/auth.md` — append "Multi-account pool" section: schema, hash semantics, OAuth-vs-API-key tradeoff, kubectl recipe for out-of-band Secret creation.

No changes to core Python (`prefect_orchestration/`) — entrypoint is shell, and the auth picker runs entirely inside the container.

## Approach

### Entrypoint logic (`docker/entrypoint.sh`)

Insert a "pool resolution" stanza **before** the existing OAuth/apikey precedence block, gated on the single-env vars being unset (single-env wins by spec):

```
if [[ -z "${CLAUDE_CREDENTIALS:-}" && -n "${CLAUDE_CREDENTIALS_POOL:-}" ]]; then
  pool_size = jq 'length' on $CLAUDE_CREDENTIALS_POOL
  if PO_CREDENTIALS_POOL_INDEX set, idx = that
  else idx = _po_pick_index "$HOSTNAME" "$pool_size"
  CLAUDE_CREDENTIALS = jq -c ".[$idx]" of pool
  PO_AUTH_POOL_INDEX=$idx; PO_AUTH_POOL_SIZE=$pool_size
fi
unset CLAUDE_CREDENTIALS_POOL
```

Mirror block for `ANTHROPIC_API_KEY_POOL` → `ANTHROPIC_API_KEY` (string element, not object). Same precedence + same `_INDEX` test override.

Hash function:

- **Ordinal fast path**: `HOSTNAME` matching `*-<int>$` (StatefulSet ordinals: `worker-7`) uses the trailing integer mod `pool_size`. Guarantees exact even spread when `replicas % size == 0`.
- **Hash fallback**: `printf '%s' "$HOSTNAME" | sha256sum | cut -c1-8` taken as hex32, mod `pool_size`. Statistically even, not exact.

Implementation notes:

- `jq` is already in the worker base image (Dockerfile line 95) — no new dep.
- **Audit log**: emit `po-entrypoint: auth=… source=… pool index=<i> size=<n>` only when a pool was used. Single-env runs keep the current single-line format. Index/size only — never credential bodies.
- **Scrub** `CLAUDE_CREDENTIALS_POOL` and `ANTHROPIC_API_KEY_POOL` from environ via `unset` before `exec` so child processes / `/proc/<pid>/environ` don't leak the array.
- **Malformed JSON / empty array / out-of-range index**: log error and exit 64. Don't silently fall through to apikey — that hides config bugs.
- **`set -x` guardrail**: existing test (`test_entrypoint_does_not_set_x`) continues to enforce no shell-trace mode.

### Chart wiring

`values.yaml` additions under `auth.{apikey,oauth}.pool`:

```yaml
pool:
  enabled: false
  size: 0                    # asserted == len(...) when createSecret
  secretName: <pool-name>
  secretKey: pool
  createSecret: false
  credentials: []  / apiKeys: []
```

When `pool.enabled`, the worker Deployment swaps the env name from `CLAUDE_CREDENTIALS` / `ANTHROPIC_API_KEY` to `_POOL` variant, sourcing from the configured Secret. Single-key and pool paths are mutually exclusive on a given Deployment. Single-key Secret template is gated on `not pool.enabled` so the chart never renders both.

Stick to the **single-key-holds-JSON-array** strategy (triage option B): one Secret with key `pool` whose value is the JSON array. Keeps entrypoint logic identical to the env-var spec; avoids chart loops over indexed keys.

`HOSTNAME` is automatically exported by the kubelet to every container — no downward-API env needed.

### Doc update

`engdocs/auth.md` — new `## Multi-account pool` section covering:
1. When to use it (rate-limit fanout > single account).
2. Schema and precedence (single-env wins).
3. Hash semantics + ordinal-hostname fast path.
4. OAuth vs API-key tradeoffs (sub-billing vs higher RPS).
5. Out-of-band Secret recipes (`kubectl create secret generic …`).
6. `_INDEX` test override.
7. Caveats: hash uniformity for non-ordinal hostnames; replicas-as-multiple-of-poolSize advice.

## Acceptance criteria (verbatim from issue)

- Worker entrypoint logs which credentials index it picked at boot.
- 100 replicas with poolSize=10 spread evenly (each account gets ~10 replicas).
- Single-account mode (CLAUDE_CREDENTIALS) still works unchanged.
- Tests: unit (entrypoint shell logic via bats or python wrapper), e2e (kind cluster with 5 replicas + 2-account pool, verify 2-3 replicas land on each account).
- engdocs/auth.md updated with the pool recipe.

Plus the bd `acceptance` field: "Pool routing works deterministically; single-account fallback preserved; chart wiring; tests; doc".

## Verification strategy

| AC | Concrete check |
|---|---|
| Logs index at boot | `tests/test_docker_entrypoint.py::test_oauth_pool_picks_slot_by_ordinal_hostname` and `…apikey_pool_picks_slot` — assert stderr contains `pool index=<i> size=<n>`, and **never** the credential body. |
| 100 replicas spread evenly | `test_pool_ordinal_distribution_is_perfect`: simulate 100 hostnames `worker-0..worker-99` with `poolSize=10`; assert each index 0–9 picked exactly 10 times (ordinal fast path). Plus `test_pool_hash_distribution_is_reasonable` on synthetic non-ordinal names asserting every bucket hit and max bucket ≤ 2× expected. |
| Single-account unchanged | Existing `test_oauth_mode_materializes_credentials` + `test_oauth_via_bindmount_credential_file` + `test_apikey_fallback_when_no_oauth` continue to pass; new `test_single_credentials_beats_pool` and `test_single_apikey_beats_apikey_pool` confirm pool is ignored when single env present. |
| Unit tests for pool logic | Python subprocess tests in `tests/test_docker_entrypoint.py` (no bats — Python is the convention here, `bash -n` syntax check already runs). |
| e2e (kind + 5 replicas + 2 accounts) | Heavyweight kind+real-account e2e is **deferred to manual smoke** per the cost concern in triage.md. Recipe is documented in `engdocs/auth.md` so an operator can run it on demand. The deterministic distribution unit tests cover the routing logic without requiring real Anthropic accounts; that is the substantive AC. |
| Chart wiring | `tests/test_helm_chart.py::test_oauth_pool_wires_pool_env`, `test_apikey_pool_wires_pool_env`, `test_oauth_pool_create_secret_renders_pool_secret`, `test_apikey_pool_size_mismatch_fails`, `test_oauth_pool_create_secret_requires_credentials`. `helm lint` passes; `values.schema.json` validates. |
| Doc updated | `tests/test_helm_chart.py::test_auth_md_documents_pool` — grep `engdocs/auth.md` for `CLAUDE_CREDENTIALS_POOL` and `ANTHROPIC_API_KEY_POOL`. |

## Test plan

- **unit** (`tests/test_docker_entrypoint.py`, `tests/test_helm_chart.py`):
  - Pool resolves index from ordinal HOSTNAME (`worker-7` → `7 % size`).
  - Pool resolves index from sha256-mod path for non-ordinal hostnames.
  - `PO_CREDENTIALS_POOL_INDEX` / `PO_API_KEY_POOL_INDEX` overrides beat hash.
  - `CLAUDE_CREDENTIALS` (single) beats `CLAUDE_CREDENTIALS_POOL`; same for API key.
  - Malformed pool JSON / empty array / out-of-range index exits 64 with clear error.
  - Audit log line contains `pool index=N size=M` and nothing else from the pool body.
  - 100-hostname distribution: ordinal path exact (10 each); hash path max bucket ≤ 2× expected.
  - `helm template` env+secret rendering for both pool variants.
  - `helm template` size-mismatch fail; `createSecret` requires a populated list.
  - `engdocs/auth.md` mentions both pool envs.
- **e2e**: not added in this iteration. Distributed-routing logic is deterministic and exercised by the unit tests; a real kind cluster + 2 Anthropic accounts is manual smoke per triage cost concern. Recipe documented in auth.md.
- **playwright**: N/A (no UI).

## Risks

- **Hash uniformity for non-ordinal hostnames** (Deployment-style suffixes like `po-worker-7d4f9-abcde`): sha256-mod is statistically even, not exact. For 100 / 10 stdev ≈ 3, worst-case bucket 13–14. Ordinal fast path mitigates when running as a StatefulSet; documented in auth.md.
- **`jq` dependency**: already in image; no new build cost.
- **Secret payload size**: 100 OAuth blobs (~2 KB each) ≈ 200 KB; under the 1 MiB Secret limit. Documented.
- **Backwards compatibility**: existing `CLAUDE_CREDENTIALS` / `ANTHROPIC_API_KEY` paths remain pixel-identical. Pool resolution is a *prefix* that only activates when both single envs are absent — preserves all current entrypoint tests unchanged.
- **No API contract change**: pool envs are container-internal; no PO Python API surface, no flow signature, no Prefect deployment param touched.
- **Log scraping**: any aggregator alerting on the literal `auth=oauth` line will see a `pool index=` suffix when the pool is enabled — minor format change, documented in auth.md.
- **`set -x` regression**: the new pool block uses `jq` and arithmetic; existing `test_entrypoint_does_not_set_x` guard catches accidental debug traces.
- **Migration**: none. New fields are opt-in; default `pool.enabled=false` keeps existing deployments unchanged.
