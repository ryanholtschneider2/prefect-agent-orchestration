# Work pools: running PO on Kubernetes and Docker

The playbook for running `software-dev-full` (and any other PO formula)
against a non-`process` Prefect work pool. Two paths:

1. **Local docker-compose** — laptop dev / smoke
2. **Kubernetes** — scale-out / cloud

Both use the same image: `Dockerfile` produces `po-worker:base`
(ubuntu:24.04 + node22 + tmux + git + uv + bd + Claude Code +
`prefect-orchestration`); `Dockerfile.pack` overlays a formula pack on
top, so per-pack images are one cheap rebuild away from the base.

## Image shape

| Layer | Path | What it adds |
|---|---|---|
| base | `Dockerfile` (target `runtime`) | OS, tools, core, non-root `coder` user, entrypoint |
| overlay | `Dockerfile.pack` | one (or more) `po-formulas-*` packs |

```bash
# Base only — has `po doctor`, `po list` (empty), no formulas.
docker build -t po-worker:base .

# Base with a sibling pack repo baked in (single image, no overlay):
docker build --build-context pack=../software-dev/po-formulas \
             -t po-worker:dev .

# Or: keep base stable, overlay a published pack:
docker build -t po-worker:software-dev \
    --build-arg BASE=po-worker:base \
    --build-arg PACK_SPEC=po-formulas-software-dev==X.Y.Z \
    -f Dockerfile.pack .
```

### Image rebuild cadence

- **Pack-only changes** — rebuild the overlay only; base stays cached.
- **Toolchain bumps** (`BD_VERSION`, Node version) — bump build-args
  and rebuild base.
- **Pinned releases** — set `PACK_SPEC=po-formulas-software-dev==X.Y.Z`
  so a `po update` inside a long-lived pod is a no-op (the pack is
  pinned at image build time; pod restart on pack version bump).

## Local docker-compose

Three services in `docker-compose.yml`:

| Service | Role |
|---|---|
| `prefect-server` | Prefect API + UI on `http://127.0.0.1:4200` |
| `worker` | `prefect worker start --pool po` against the bind-mounted rig |
| `client` (profile) | One-shot driver: `docker compose run --rm client …` |

```bash
mkdir -p rig && (cd rig && bd init)

# Real Claude (requires API key on host):
export ANTHROPIC_API_KEY=sk-…
ISSUE_ID=demo-1 PO_BACKEND=cli ./scripts/smoke-compose.sh

# Stub (no API key required, exercises wiring only):
ISSUE_ID=demo-1 PO_BACKEND=stub ./scripts/smoke-compose.sh
```

The smoke script defaults to `PO_BACKEND=stub` so it does not need an
Anthropic key. Flip to `cli` when you want a real run.

## Kubernetes

### 1. Build + push the image

```bash
docker build -t <registry>/po-worker:<tag> \
    --build-context pack=../software-dev/po-formulas .
docker push <registry>/po-worker:<tag>
```

### 2. Apply cluster pre-reqs

```bash
kubectl apply -f k8s/po-rig-pvc.yaml

# Real secret (the YAML at k8s/anthropic-api-key.example.yaml is a
# documentation stub only — do not commit a real key):
kubectl create secret generic anthropic-api-key \
    --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"
```

### 3. Create the pool with the base-job-template

```bash
prefect work-pool create po-k8s --type kubernetes \
    --base-job-template k8s/po-base-job-template.json
```

The template sets the image, mounts the `po-rig` PVC at `/rig`, mounts
`anthropic-api-key` as `ANTHROPIC_API_KEY`, and forces `PO_BACKEND=cli`.
Edit the file before applying if your cluster uses a different
namespace, registry, or storage class.

### 4. Pin a deployment to the pool

In your pack's `register()`:

```python
import os
from prefect.client.schemas.schedules import CronSchedule
from po_formulas.flows import epic_run

def register():
    pool = os.environ.get("PO_DEFAULT_WORK_POOL")  # e.g. "po-k8s"
    deps = [
        epic_run.to_deployment(
            name="nightly", schedule=CronSchedule(cron="0 9 * * *"),
        ),
    ]
    if pool:
        for d in deps:
            d.work_pool_name = pool
    return deps
```

Apply:

```bash
PO_DEFAULT_WORK_POOL=po-k8s po deploy --apply
```

`po doctor` will WARN if any pinned deployment references a pool that
doesn't exist — fix it with `prefect work-pool create`.

### 5. Run the worker

```bash
kubectl apply -f k8s/po-worker-deployment.yaml
kubectl logs -f deployment/po-worker
```

### 6. Trigger a run

```bash
prefect deployment run epic_run/nightly \
    --param epic_id=<id> --param rig=demo --param rig_path=/rig
```

Watch the Job pod:

```bash
kubectl get jobs -l app=po-flow
kubectl logs -f job/<name>
```

## Helm install

For repeatable cluster installs, `charts/po/` packages everything above
(prefect-server, po-worker Deployment, pool-register hook Job, rig PVC,
Claude auth Secret references, optional Ingress) into a single chart.

```bash
# 1. Build + push the worker image (same as the imperative path)
docker build -t <registry>/po-worker:<tag> \
    --build-context pack=../software-dev/po-formulas .
docker push <registry>/po-worker:<tag>

# 2. Pre-create the auth Secret out-of-band (chart never embeds it)
kubectl create namespace po
kubectl -n po create secret generic anthropic-api-key \
    --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"

# 3. Install
helm install po ./charts/po -n po \
    --set worker.image.repository=<registry>/po-worker \
    --set worker.image.tag=<tag>

# 4. Smoke (idempotent; exercises the pre-install pool-register hook)
helm test po -n po
kubectl -n po rollout status deployment/po-worker
kubectl -n po logs -f deployment/po-worker
```

The chart's `pre-install` / `pre-upgrade` Helm hook runs a one-shot Job
that creates the work pool if missing, so the worker never races
pool registration. It is idempotent — re-installing or upgrading is
safe.

### Selecting OAuth instead of API key

```bash
kubectl -n po create secret generic claude-oauth \
    --from-file=credentials.json="$HOME/.claude/.credentials.json"

helm install po ./charts/po -n po \
    --set auth.mode=oauth \
    --set auth.oauth.persistence.enabled=true   # opt-in: survive pod restarts
```

`auth.oauth.persistence.enabled=true` provisions an RWO PVC mounted at
`/home/coder/.claude` so the Claude CLI's in-place token refreshes
survive pod restarts (see [`auth.md`](auth.md) "Opt-in persistence",
beads `prefect-orchestration-tyf.3`). Note the worker stays at one
replica because RWO can only be mounted on a single node.

### Storage: RWX vs RWO

The chart defaults to `rig.accessMode=ReadWriteOnce` + 20 GiB so kind /
minikube installs work out of the box (their bundled `standard`
storageClass is RWO-only). For multi-replica workers on EKS / GKE /
bare-metal:

```bash
helm install po ./charts/po -n po \
    --set rig.accessMode=ReadWriteMany \
    --set rig.storageClass=efs-sc \
    --set worker.replicaCount=3
```

`po-rig.accessMode=ReadWriteOnce` with `worker.replicaCount>1` will
schedule pods to a single node (or fail) — `NOTES.txt` warns on this
combination after install.

### Optional Prefect-UI Ingress

Off by default. Enable per-cluster:

```bash
helm install po ./charts/po -n po \
    --set ingress.enabled=true \
    --set ingress.className=nginx \
    --set 'ingress.hosts[0].host=prefect.example.com' \
    --set 'ingress.hosts[0].paths[0].path=/' \
    --set 'ingress.hosts[0].paths[0].pathType=Prefix'
```

cert-manager wiring is intentionally not bundled — pass annotations +
`tls:` via values for your cluster's setup.

### Cloud smoke (kind / Hetzner)

End-to-end validation of `chart + image + bd + software-dev-full` on a
real cluster lives at `scripts/cloud-smoke/`. See
[`engdocs/cloud-smoke.md`](cloud-smoke.md) — one orchestrator drives
provision → install → seed → trigger → exit-gate → tear down, with
`kind` as the default driver and `Hetzner k3s` as the documented cloud
upgrade path. Tracked under `prefect-orchestration-tyf.5`.

### Ops references

- `helm lint charts/po` — local validation (CI runs this via
  `tests/test_helm_chart.py`)
- `helm template po ./charts/po | kubectl apply --dry-run=server -f -` —
  cluster-side dry run before a real install
- `kubectl -n po describe job/po-pool-register` — debug the
  pre-install hook if `helm install` hangs
- `kubectl -n po get cm claude-context-overrides` — verify the
  optional `~/.claude` overlay (see `scripts/sync-claude-context.sh`,
  beads `prefect-orchestration-tyf.2`)

## Auth: API key vs OAuth

See [`engdocs/auth.md`](auth.md) for the full decision matrix
(precedence rules, OAuth refresh persistence, opt-in PVC). Quick
summary:

Workers default to `ANTHROPIC_API_KEY`. The entrypoint
(`docker/entrypoint.sh`) bootstraps `~/.claude.json` so Claude Code
skips onboarding and accepts the key without a TTY prompt — modeled on
the rclaude prior art.

| Scenario | Auth |
|---|---|
| k8s worker pod | `Secret` → `ANTHROPIC_API_KEY` env var (canonical) |
| compose worker | `ANTHROPIC_API_KEY` from host env / `.env` |
| compose client (one-shot) | `PO_BACKEND=stub` skips auth entirely |
| laptop dev preferring Claude.ai subscription | uncomment the OAuth bind in `docker-compose.yml` (`~/.claude/.credentials.json:ro`) |

The user-global rule "never use API keys for local dev" still applies
to ad-hoc scripts you run on your laptop — that's why the OAuth bind
fallback exists for compose. In a deployed cluster pod, the API-key
path is correct.

## Backend selection

`prefect_orchestration.backend_select.select_default_backend()` is the
canonical chooser. Order:

| `PO_BACKEND` | tmux on PATH | stdout TTY | Backend |
|---|---|---|---|
| `cli` | — | — | `ClaudeCliBackend` |
| `tmux` | yes | — | `TmuxClaudeBackend` |
| `tmux` | no | — | **errors** (no silent fallback) |
| `stub` | — | — | `StubBackend` |
| unset (auto) | yes | yes | `TmuxClaudeBackend` |
| unset (auto) | yes | no | `ClaudeCliBackend` (container case) |
| unset (auto) | no | — | `ClaudeCliBackend` |

The image installs `tmux` so a human can `kubectl exec -it … bash` and
attach to a session manually for debugging. At normal pod runtime
there is no TTY, so the helper picks `ClaudeCliBackend` automatically.
The image still sets `ENV PO_BACKEND=cli` to make the choice explicit.

The pack-side default in `software_dev.py` continues to work; new
packs should prefer `select_default_backend()` so the TTY check is
applied uniformly.

## Rig-state strategy

| Strategy | When | Pros | Cons |
|---|---|---|---|
| **Bind mount (compose) / RWX PVC (k8s)** | now | simple; one rig per epic; `bd` claim guarantees single-writer per issue | requires RWX storage class (EFS/NFS/Filestore); no isolation between epics |
| **Ephemeral per-run workspace** (init-container clone + post-step push) | after `bd dolt push` + git remote | cloud-native; per-run isolation | requires git remote (this repo has none) and `bd` Dolt server-mode |

Pick the bind-mount/PVC path now. Ephemeral clone+push is a sibling
bead, deferred until prerequisites land.

## Concurrency limits

`prefect concurrency-limit` is global — `builder` and `critic`
tag-based caps remain in effect when tasks run on a `po-k8s` pool the
same way they do on the local `process` pool. No tag- or pool-specific
config beyond the existing `prefect concurrency-limit create` calls in
the project README.

## `po doctor` checks

- **Work pool exists** — at least one pool registered server-side.
- **Deployment pools exist** — every deployment whose `register()`
  pins `work_pool_name` references a pool that exists. WARN on miss
  (not FAIL — many users `po deploy` without `--apply`); OK when no
  deployment pins a pool. Skipped when `PREFECT_API_URL` is unset.

Both checks are in `prefect_orchestration/doctor.py` and run as part
of `po doctor`.

## Known limitations

- **OAuth secrets in k8s** — supported as opt-in (see
  [`engdocs/auth.md`](auth.md) "Opt-in persistence"). Default k8s
  path remains API-key for production.
- **Multi-tenant rig isolation** — out of scope. One rig PVC per
  cluster; `bd` claim discipline is the only writer guarantee.
- **Ephemeral rig (clone+push)** — deferred (see "Rig-state strategy").
