# Auth: Claude Code authentication for PO workers

Two auth modes, three persistence stories. This page is the canonical
reference; `engdocs/work-pools.md` links here.

## TL;DR

| Pod role | Default auth | Persistence story |
|---|---|---|
| Production worker (k8s) | `ANTHROPIC_API_KEY` Secret | irrelevant — API keys don't expire/refresh |
| Long-lived dev worker (k8s, OAuth) | `CLAUDE_CREDENTIALS` Secret | seed-on-first-boot + opt-in PVC at `$HOME/.claude` (option a) |
| Per-flow-run Job pod | `ANTHROPIC_API_KEY` (template default) | n/a — short-lived |
| docker-compose worker | bind-mount `~/.claude/.credentials.json:ro` | host file is the source of truth |

## Auth modes

The entrypoint (`docker/entrypoint.sh`) supports two modes with the
following precedence:

1. **OAuth from on-disk file** — `$HOME/.claude/.credentials.json`
   exists and is non-empty. Used as-is. `CLAUDE_CREDENTIALS` and
   `ANTHROPIC_API_KEY` are unset before exec. Audit log:
   `po-entrypoint: auth=oauth source=disk`.
2. **OAuth from env** — `CLAUDE_CREDENTIALS` JSON blob materialized to
   the credentials file (mode 0600). `ANTHROPIC_API_KEY` unset.
   Audit: `auth=oauth source=env`.
3. **API key** — `ANTHROPIC_API_KEY` env var. Audit: `auth=apikey`.

The on-disk-first ordering matters: it's what makes a PVC mount at
`$HOME/.claude/` actually persistent across pod restarts. Without it,
every restart would clobber the freshly-refreshed token with the
(stale) Secret payload.

## Why this matters: refresh-token persistence

Claude CLI refreshes OAuth access tokens in place by writing to
`~/.claude/.credentials.json`. In a pod that path is ephemeral — on
restart the next `claude` invocation reads whatever was last seeded
into the container, not whatever the CLI last refreshed. Three options
were on the table for tyf.3:

| Option | What | Status |
|---|---|---|
| (a) PVC mount at `$HOME/.claude/` | Per-pod RWO PVC, refresh-writes survive restarts | **Implemented as opt-in** |
| (b) Sidecar that watches creds file mtime, syncs back to Secret | Clean but requires `secrets/update` RBAC in-namespace | **Rejected** — RBAC blast radius is too high for a dev-ergonomics fix |
| (c) Document "restart < refresh-window, else use API key for prod" | Cheapest, matches the global "OAuth for dev / API key for prod" rule | **Implemented as default** |

Anthropic does not publicly document the refresh-token sliding window
(believed ~30d). We don't depend on the exact value — option (a) makes
restart cadence irrelevant for OAuth pods that opt in; option (c) keeps
prod on API keys, which never refresh.

## Default policy (option c)

For production pods, set `ANTHROPIC_API_KEY`. The credentials never
refresh, the entrypoint never writes a credentials file, and pod
restarts are completely indifferent to auth state. This is the
canonical k8s path documented in `engdocs/work-pools.md`.

For dev pods using OAuth, restart cadence must stay inside the
refresh-token sliding window — or the pod will fail at `claude --print`
the next time it starts, and you'll need to re-create the
`claude-oauth` Secret from a fresh local login. If that's too fragile,
opt into PVC persistence (below).

## Opt-in persistence (option a)

Apply the PVC and switch the worker Deployment to OAuth:

```bash
# 1. Create the OAuth Secret (one-time, from your local Claude.ai login)
kubectl create secret generic claude-oauth \
    --from-file=credentials.json="$HOME/.claude/.credentials.json"

# 2. Provision the persistence volume
kubectl apply -f k8s/claude-oauth-pvc.example.yaml

# 3. Patch po-worker-deployment.yaml: uncomment the `claude-home` volume
#    + volumeMount blocks and switch the env from ANTHROPIC_API_KEY to
#    CLAUDE_CREDENTIALS (snippet at the bottom of the example file).
kubectl apply -f k8s/po-worker-deployment.yaml
```

On first boot the PVC is empty; the entrypoint materializes the
credentials file from `CLAUDE_CREDENTIALS`. On every subsequent boot
the on-disk file (potentially refreshed by the CLI mid-life) wins and
the env-seed is skipped — that's the persistence behavior.

### Constraints

- **RWO ⇒ `replicas: 1`.** Cannot scale the worker Deployment beyond
  one pod while the PVC is attached. Horizontal scale requires
  StatefulSet + per-pod PVCs (out of scope) or API-key auth.
- **Per-pod, not per-cluster.** The PVC binds to one pod; this pattern
  doesn't shard credentials across multiple workers.
- **Not for per-flow-run Job pods.** `po-base-job-template.json`
  spawns short-lived pods — refresh persistence isn't needed and PVC
  binding would serialize Jobs.

### Rotation

The Secret stays the source of *first-boot* truth; we don't write back
to it. To force a re-seed (e.g. after rotating `claude-oauth` from a
new local login):

```bash
kubectl exec -it deployment/po-worker -- rm /home/coder/.claude/.credentials.json
kubectl rollout restart deployment/po-worker
```

Or delete + recreate the PVC.

## Tested-restart procedure (manual smoke)

This is the AC3 verification step from tyf.3. Wire it into the cloud
smoke (tyf.5) for OAuth-mode coverage.

```bash
# Pre-req: option (a) applied per the snippet above.

# 1. Confirm pod is up and OAuth path is in use.
kubectl logs deployment/po-worker | grep 'po-entrypoint: auth='
# expect: auth=oauth source=env  (first boot)
# or:     auth=oauth source=disk (subsequent boot)

# 2. Drop a sentinel into the credentials file to prove persistence.
kubectl exec deployment/po-worker -- sh -c \
  'jq ".sentinel=\"tyf3-smoke\"" /home/coder/.claude/.credentials.json \
   > /tmp/c && mv /tmp/c /home/coder/.claude/.credentials.json'

# 3. Force a restart.
kubectl delete pod -l app=po-worker
kubectl wait --for=condition=Ready pod -l app=po-worker --timeout=60s

# 4. Confirm sentinel survived AND auth=oauth source=disk.
kubectl exec deployment/po-worker -- jq -r .sentinel \
  /home/coder/.claude/.credentials.json
# expect: tyf3-smoke
kubectl logs deployment/po-worker | tail -1 | grep 'source=disk'

# 5. Confirm Claude actually authenticates with the persisted file.
kubectl exec deployment/po-worker -- claude --print "say ok"
# expect: a non-error response
```

## Why option (b) is rejected

The sidecar approach (watch credentials.json mtime, write back to the
k8s Secret via the API) was considered and rejected:

- Requires a ServiceAccount with `secrets/update` in the namespace.
  Any compromise of the sidecar (or the worker container that shares
  its filesystem) yields write access to OAuth credentials.
- Solves a problem option (a) already solves with kubelet primitives
  and zero RBAC surface area.
- Adds a moving part (the watcher) for a single file.

If a future requirement forces multi-pod OAuth scale-out where (a)'s
RWO + per-pod constraint breaks down, revisit (b) with an audited
narrow-scope ServiceAccount.

## See also

- `docker/entrypoint.sh` — the actual auth bootstrap.
- `tests/test_docker_entrypoint.py` — exercises every precedence path.
- `k8s/claude-oauth-pvc.example.yaml` — the opt-in persistence PVC.
- `engdocs/work-pools.md` — overall k8s/compose deployment playbook.
