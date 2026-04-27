# Decision log — prefect-orchestration-5wk.3 (build iter 1)

- **Decision**: Single-key Secret holds the JSON array (not indexed `pool-0`/`pool-1`/… keys).
  **Why**: Plan §"Chart wiring" + triage option B. Keeps entrypoint logic identical to the env-var spec and avoids chart loops over indexed keys. Operators can still build the array from individual files via `kubectl create secret … --from-file=pool=<(jq -s . cred1.json …)`.
  **Alternatives considered**: Indexed keys (`pool-0..pool-N`) — friendlier to GitOps diffs but requires chart-side iteration and a more complex entrypoint join.

- **Decision**: Ordinal-hostname fast path (`worker-N` → `N % size`) + sha256-mod fallback.
  **Why**: AC #2 demands "100 replicas with poolSize=10 spread evenly" — sha256-mod alone is statistically even, not exact. StatefulSet ordinals + ordinal pick gives perfect spread when `replicas % size == 0`.
  **Alternatives considered**: Pure sha256-mod (rejected: stdev ≈ 3 for 100/10, worst bucket 13–14, fails "evenly"); CRC32 (rejected: same statistical issue, no portability win over sha256sum which is in the base image).

- **Decision**: Single-env (`CLAUDE_CREDENTIALS` / `ANTHROPIC_API_KEY`) wins over `_POOL`.
  **Why**: Plan §"Approach" + bd issue spec. Pool resolution runs *before* the existing OAuth/apikey block as a prefix that only activates when the single env is empty — preserves all current entrypoint behaviour and tests unchanged.
  **Alternatives considered**: Pool wins over single — rejected; would silently break existing single-account deployments.

- **Decision**: Malformed JSON / empty array / out-of-range index exits 64.
  **Why**: Don't silently fall through to apikey path — that would hide config bugs. Exit 64 (EX_USAGE) matches the existing "no auth configured" exit.
  **Alternatives considered**: Falling through to apikey on bad pool JSON (rejected: silent failure mode), warning-only with default index 0 (rejected: same).

- **Decision**: Audit-log line gains `pool index=<i> size=<n>` suffix only when pool used; single-env runs keep current format.
  **Why**: Minimal log-format change for existing consumers; only emits new fields when pool is in play. Index/size only — never credential bodies.
  **Alternatives considered**: Always emit `pool=` (rejected: noise for the common single-env case).

- **Decision**: Single-key Secret template gated on `not pool.enabled` (mutually exclusive with pool Secret).
  **Why**: Chart never renders both — a deployment uses either single-key or pool, never both. Cleaner than `fail`-level guards inside the template.
  **Alternatives considered**: `helm template` `fail` if both `createSecret` are true (more vocal, but doesn't help when both blocks just reference existing Secrets).

- **Decision**: Pool envs (`CLAUDE_CREDENTIALS_POOL`, `ANTHROPIC_API_KEY_POOL`) `unset` before `exec`.
  **Why**: Same scrub discipline as the existing single-env path — keeps the array out of `/proc/<pid>/environ` for child processes.
  **Alternatives considered**: Leave them set (rejected: leaks the full pool body to flow workers).

- **Decision**: kind+real-account e2e deferred to manual smoke; recipe in `engdocs/auth.md`.
  **Why**: Plan §"Test plan" + triage cost concern. Distribution AC is fully exercised by deterministic unit tests (100 ordinal hostnames; 100 random hostnames). Real Anthropic accounts in CI are out of scope; the routing logic is independent of whether the credentials authenticate.
  **Alternatives considered**: Add a kind-based e2e (rejected: needs real creds in CI, slow, brittle), use a docker-compose stub e2e (deferred — unit tests already cover the full routing logic; the only thing compose would add is "pod gets the expected slot" which is identical to the unit subprocess test on hostnames).

- **Decision**: No schema-level enforcement that `pool.size > 0` only when `enabled=true`.
  **Why**: Plan critic nit #2 — but JSON Schema's "if/then" for cross-field validation pushes complexity into a static schema for marginal benefit. Default `size: 0` + `enabled: false` is meaningless-but-harmless; the chart's `fail` checks fire only when `enabled=true && createSecret=true`, which is when `size` actually matters.
  **Alternatives considered**: Add `if {enabled: true}, then {size: {minimum: 1}}` — rejected as over-engineering for a default that nobody runs.
