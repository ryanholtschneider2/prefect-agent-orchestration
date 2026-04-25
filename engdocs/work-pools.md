# Work pools: running PO on Kubernetes and Docker

This is the playbook for running `software-dev-full` (and any other PO
formula) against a non-`process` Prefect work pool. Two paths:

1. **Local docker-compose** — the smoke path, suitable for laptop dev
2. **Kubernetes** — the scale-out path, suitable for CI / cloud

The image at `Dockerfile` bundles `uv` + `bd` + `claude` CLI +
`prefect-orchestration` + a configurable formula pack, so the same image
serves as either a Prefect worker host or an interactive `po run` driver.

## Image build

```bash
# Default: software-dev pack
docker build -t po-worker:dev .

# Override the pack (any uv-installable spec)
docker build -t po-worker:dev \
    --build-arg PACK_SPEC=po-formulas-software-dev .

# Faster iteration on docs (skip the runtime stage)
docker build --target tools -t po-tools:dev .
```

The build is multi-stage: the `tools` stage produces `uv`, `bd`, and the
globally-installed `claude` Node package; the `runtime` stage copies those
artifacts into a Python 3.13 slim base and runs `uv tool install` for
core + pack. Total image is ~500 MB; the `tools` stage rebuilds rarely
and cache-hits aggressively.

### Image rebuild cadence

- **Pack-only changes** — rebuild and push (no version-of-record;
  re-tag `po-worker:dev` or pin to a date tag like `po-worker:2026-04-25`).
- **Pinned formula pack releases** — set `PACK_SPEC=po-formulas-software-dev==X.Y.Z`
  at build time so the image's entry-point metadata matches the release.
- **Toolchain bumps** (`BD_VERSION`, `NODE_VERSION`, `PYTHON_VERSION`)
  are build-args at the top of the Dockerfile — bump and rebuild.

## Local docker-compose smoke

The `docker-compose.yml` ships three services:

| Service | Role |
|---|---|
| `prefect-server` | Prefect API + UI on `http://127.0.0.1:4200` |
| `worker` | `prefect worker start --pool po` against the bind-mounted rig |
| `client` (profile) | One-shot driver: `docker compose run --rm client …` |

Driver script:

```bash
# Pre-req: a rig directory with .beads/ (run `bd init` once in it)
mkdir -p rig && (cd rig && bd init)

ISSUE_ID=demo-1 RIG_DIR=./rig PO_BACKEND=stub ./scripts/smoke-compose.sh
```

`PO_BACKEND=stub` is the default — it short-circuits Claude calls so the
smoke exercises Prefect + bd wiring without requiring OAuth credentials.
Flip to `PO_BACKEND=cli` once `~/.claude/.credentials.json` is mounted.

## Kubernetes work-pool path

Prefect's native `kubernetes` work-pool type runs each flow as a pod.

### 1. Push the image

```bash
docker tag po-worker:dev <registry>/po-worker:<tag>
docker push <registry>/po-worker:<tag>
```

### 2. Create the pool

```bash
prefect work-pool create po-k8s --type kubernetes \
    --base-job-template kubernetes-job.json
```

Edit the base job template so each pod uses your image, mounts the rig
PVC, and (short-term) the OAuth secret — see "OAuth in containers" below.

### 3. Pin a deployment to the pool

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

Then apply:

```bash
PO_DEFAULT_WORK_POOL=po-k8s po deploy --apply
```

`po doctor` will WARN if any pinned deployment references a pool that
doesn't exist on the server — fix it with `prefect work-pool create`.

### 4. Start a worker

```bash
kubectl apply -f k8s/po-worker-deployment.yaml
# or, ad-hoc:
kubectl run po-worker --image=<registry>/po-worker:<tag> -- \
    prefect worker start --pool po-k8s
```

### 5. Trigger a run

```bash
prefect deployment run epic_run/nightly \
    --param epic_id=<id> --param rig=demo --param rig_path=/rig
```

## Rig-state strategy

Two real options. **Pick the bind-mount/PVC path for now**; ephemeral
clone+push is deferred until a git remote and `bd` Dolt server-mode are
in place for this repo.

| Strategy | When | Pros | Cons |
|---|---|---|---|
| **Bind mount (compose) / RWX PVC (k8s)** | now | simple; one rig per epic; bd claim guarantees single-writer | requires RWX storage class (EFS/NFS); no isolation between epics |
| **Ephemeral per-run workspace** (init-container clone + post-step push) | after `bd dolt push` + git remote | cloud-native; per-run isolation | requires git remote (this repo has none); requires bd in server mode |

Single-writer-per-rig is enforced by `bd update --claim` inside each
flow step — concurrent epics MUST use separate rigs/PVCs.

## Backend behavior in containers

The image deliberately omits `tmux`. PO's auto-selection
(`software_dev.py`) does `TmuxClaudeBackend if shutil.which("tmux") else
ClaudeCliBackend`, so containers fall back to `ClaudeCliBackend` cleanly.
The Dockerfile sets `ENV PO_BACKEND=cli` to make this explicit — anyone
reading pod logs sees the choice up front.

`PO_BACKEND=tmux` will hard-error inside a pod (intentional — no TTY,
no point in silently falling back). `PO_BACKEND=stub` is the right
choice for smokes that don't need Claude.

## OAuth in containers (known limitation)

Out of scope for the j2p bead; tracked as a sibling. Today:

- **Compose**: bind-mount `~/.claude/.credentials.json:ro`. Works on a
  laptop, doesn't survive in CI/cloud.
- **k8s**: mount as a `Secret`. The credentials file is a long-lived
  OAuth refresh token — treat it as production credential material,
  rotate via the Claude CLI after manual login, and never commit.

## Concurrency limits across pools

`prefect concurrency-limit` is global — `builder` and `critic` tag-based
caps remain in effect when tasks run on a `po-k8s` pool the same way
they do on the local `process` pool. No tag- or pool-specific config
needed beyond the existing `prefect concurrency-limit create` calls
documented in the project README.

## Doctor checks

`po doctor` adds one container-aware check:

- **Deployment pools exist** — for every deployment whose `register()`
  pins `work_pool_name`, verify the pool exists on the configured
  Prefect server. WARN (not FAIL) on miss; OK when no deployment pins
  a pool. Skipped when `PREFECT_API_URL` is unset.
