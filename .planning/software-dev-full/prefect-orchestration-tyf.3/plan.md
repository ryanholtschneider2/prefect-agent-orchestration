# Plan — prefect-orchestration-tyf.3

OAuth token refresh persistence across pod restarts.

## Decision

Implement **option (c) as default + option (a) as opt-in**, with a small
entrypoint change so both paths are correct:

1. **Default policy (c):** for production, use `ANTHROPIC_API_KEY`. For
   OAuth (dev/non-prod), document that pods must restart inside the
   refresh-token sliding window (~30d believed; we don't depend on the
   exact value), or accept a manual re-seed of the Secret when they
   don't. This matches the global rule already in `~/.claude/CLAUDE.md`
   ("OAuth for dev / API key for prod").
2. **Opt-in persistence (a):** support an optional PVC mount at
   `$HOME/.claude/` so OAuth refreshes survive restarts for long-lived
   dev pods. Provide a `k8s/claude-oauth-pvc.example.yaml` and a
   `volumeMounts` snippet in `po-worker-deployment.yaml` (commented).
3. **Entrypoint correctness:** today the entrypoint *always* overwrites
   `$HOME/.claude/.credentials.json` from `CLAUDE_CREDENTIALS`. With a
   PVC mount, that stomps the freshly-refreshed token on every restart
   and defeats persistence. Fix: when both `CLAUDE_CREDENTIALS` is set
   *and* an existing on-disk credentials file is present, prefer the
   on-disk file if it is newer than the Secret payload (or if the file
   already validates and the env Secret is identical/older). Simplest
   correct rule: **if the file already exists and is non-empty, keep
   it; otherwise materialize from env.** (Secret rotation is a separate
   workflow; rotating in-place is not a goal of tyf.3.)

Option (b) (sidecar that writes refreshes back to the Secret) is
rejected: it requires `secrets/update` RBAC in-namespace, expanding
blast radius for what is a dev-ergonomics problem. Document this
rejection in the engdocs entry.

## Affected files

- `docker/entrypoint.sh` — keep-existing-credentials behavior; emit a
  one-line log noting which path was taken (`oauth-from-env`,
  `oauth-from-disk`, `apikey`).
- `k8s/po-worker-deployment.yaml` — comment block showing the opt-in
  PVC variant for OAuth persistence; leave default unchanged
  (API-key).
- `k8s/claude-oauth-pvc.example.yaml` — **new**. RWO PVC named
  `po-claude-home` (small, e.g. 64Mi) plus the `volumes` /
  `volumeMounts` snippet. Comment notes it's per-worker-pod (RWO) and
  not suitable for the per-flow-run Jobs Prefect launches via
  `po-base-job-template.json`.
- `engdocs/auth.md` — **new**. Records the decision matrix (env-var
  policy, when to use which, sliding-window caveat, rejection of
  sidecar approach) and links from `engdocs/work-pools.md`.
- `engdocs/work-pools.md` — one-line link into `auth.md`.
- `tests/test_docker_entrypoint.py` (existing) — add cases for:
  (i) env set + no on-disk file → file is materialized;
  (ii) env set + on-disk file present → on-disk wins, env not
  re-written;
  (iii) no env, on-disk file present → oauth path picks it up
  (existing behavior, regression guard).

No changes to `prefect_orchestration/` Python core. No changes to
`po-base-job-template.json` (per-Job ephemeral pods are inside the
sliding-window-restart bucket; PVC opt-in applies to the long-lived
worker Deployment only — documented explicitly).

## Approach

Concrete diff sketch for `docker/entrypoint.sh`:

```bash
PO_AUTH_MODE="apikey"
PO_AUTH_SOURCE=""
if [[ -s "$HOME/.claude/.credentials.json" ]]; then
  # On-disk wins (PVC-persisted refresh, or host bind-mount).
  unset CLAUDE_CREDENTIALS || true
  unset ANTHROPIC_API_KEY || true
  PO_AUTH_MODE="oauth"
  PO_AUTH_SOURCE="disk"
elif [[ -n "${CLAUDE_CREDENTIALS:-}" ]]; then
  umask 077
  printf '%s' "$CLAUDE_CREDENTIALS" > "$HOME/.claude/.credentials.json"
  chmod 600 "$HOME/.claude/.credentials.json"
  unset CLAUDE_CREDENTIALS
  unset ANTHROPIC_API_KEY
  PO_AUTH_MODE="oauth"
  PO_AUTH_SOURCE="env"
fi
echo "po-entrypoint: auth=${PO_AUTH_MODE} source=${PO_AUTH_SOURCE:-apikey}" >&2
```

The on-disk-wins branch is what makes a PVC mount actually
*persistent*: Claude CLI's in-place refresh writes to the PVC, and the
next start reads from the PVC instead of being clobbered by the
(stale) Secret.

`engdocs/auth.md` outline:

- Auth modes summary (table: mode / when / Secret used / persistence).
- Why option (c) is the default; refresh-window caveat.
- How to enable option (a): apply
  `k8s/claude-oauth-pvc.example.yaml`, switch the worker Deployment
  envFrom block, mount the PVC at `/home/coder/.claude/`. Note the
  RWO + per-pod constraint.
- Why option (b) is rejected (RBAC blast radius).
- Tested-restart procedure (kubectl delete pod + verify next claude
  call still authenticates without manual Secret edit).

## Acceptance criteria (verbatim)

- One option chosen + implemented + tested across a deliberate pod
  restart
- Decision recorded in `engdocs/work-pools.md` (or new
  `engdocs/auth.md`)
- Test: pod runs, credentials refresh happens, pod restarts, next
  claude invocation succeeds without manual intervention

## Verification strategy

- **AC1 (option implemented + tested across restart):** verified at
  two layers.
  - *Unit:* extend `tests/test_docker_entrypoint.py` to assert that
    when `$HOME/.claude/.credentials.json` exists, `CLAUDE_CREDENTIALS`
    does **not** overwrite it (read file mtime + content before/after).
  - *Integration smoke (manual / part of tyf.5):* with
    `k8s/claude-oauth-pvc.example.yaml` applied: write a sentinel
    credential file inside the PVC, `kubectl delete pod` the worker,
    confirm the new pod sees the same file (sentinel survives) and
    `claude --print "ok"` succeeds. Captured as a runbook section in
    `engdocs/auth.md`; documented as the gate for tyf.5.
- **AC2 (decision recorded):** new file `engdocs/auth.md` exists with
  the four-section outline above; `engdocs/work-pools.md` links to
  it. Verified by `grep -l "auth.md" engdocs/`.
- **AC3 (no manual intervention after restart):** the on-disk-wins
  entrypoint branch is what guarantees this when (a) is in use.
  Unit test (i)/(ii) above covers the entrypoint logic; the manual
  smoke covers the full kubectl-delete loop.

## Test plan

- **Unit:** `tests/test_docker_entrypoint.py` — three new cases as
  described. The test harness already shells out to
  `docker/entrypoint.sh` via `bash` with a fake `$HOME` and
  `exec` replaced by `env`/`true` (existing pattern in this file).
- **e2e:** none (no new CLI surface; `po` verbs unchanged).
- **playwright:** N/A — backend-only.
- **Manual smoke (documented, not automated):** `kubectl` runbook in
  `engdocs/auth.md`. Wired into tyf.5's cloud smoke as the OAuth-mode
  variant.

## Risks

- **API contract:** none. `po` CLI surface unchanged; entrypoint env
  var contract unchanged (`CLAUDE_CREDENTIALS` / `ANTHROPIC_API_KEY`
  still both supported, same precedence semantics for first-boot).
- **Behavior change for existing users:** today the entrypoint
  *re-materializes* `CLAUDE_CREDENTIALS` on every restart. After this
  change, an existing on-disk credentials file wins. For docker-compose
  users who bind-mount the file read-only this is already the behavior
  (it was the `elif` branch); the new code just generalizes it. The
  practical impact: rotating the Secret no longer takes effect on a
  pod where a credentials file already exists on the mount. Documented
  as a known limitation in `engdocs/auth.md` ("to rotate, delete the
  on-disk file or recreate the PVC").
- **PVC + multi-replica:** RWO means we cannot scale the worker
  Deployment beyond `replicas: 1` while the PVC is attached. Today
  the manifest pins `replicas: 1`; the example file calls out that
  scaling requires either RWX storage or per-pod PVCs (StatefulSet).
- **Secret hygiene:** the Secret remains the source of *first-boot*
  truth. We don't write back to it. Acceptable for dev pods; prod
  uses API key path which is unaffected.
- **Migrations / breaking consumers:** none.
