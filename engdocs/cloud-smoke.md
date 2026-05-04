# Cloud smoke

End-to-end validation that the `po` Helm chart, worker image, beads
integration, and `software-dev-full` flow all plug together on a real
cluster. Issue: [`prefect-orchestration-tyf.5`].

The harness lives at `scripts/cloud-smoke/`. One orchestrator
(`run-smoke.sh`) drives **provision → install chart → seed credentials
→ seed rig → trigger flow → assert exit-gate → tear down**. Tear-down
runs from an EXIT trap so a half-provisioned cluster never lingers,
even on Ctrl-C.

## Two drivers

| Driver | Cost | When to use |
|---|---|---|
| `kind` (default) | free | local dev / CI / day-to-day verification |
| `hetzner` | ~€0.05/hr CX22 | real cloud network round-trip + image-pull semantics |

Both drivers share the chart install + seeding + assert path; only
provision and tear-down differ.

## Pre-requisites

**kind path:** `docker`, `kind`, `kubectl`, `helm`, `bd`, `git`,
`python3`.

**Hetzner path:** the kind tools above PLUS `hcloud`, `ssh`, `scp`,
plus an authenticated Hetzner CLI context and an SSH key registered
with Hetzner whose name you pass via `HCLOUD_SSH_KEY`.

`scripts/cloud-smoke/run-smoke.sh --dry-run` exercises the wiring
without invoking `kind`, `docker`, `helm`, or `kubectl` in destructive
ways — useful for CI parsing checks.

## Happy paths

### kind (default)

```bash
export ANTHROPIC_API_KEY=sk-…    # or set SMOKE_AUTH=oauth + ~/.claude/.credentials.json
./scripts/cloud-smoke/run-smoke.sh
```

Walks through:

1. Spin up a single-node kind cluster + `registry:2` container at
   `127.0.0.1:5001`, wired together via containerd mirror config.
2. `docker build` `po-worker:smoke` (with `software-dev` pack baked
   in if `packs/po-formulas-software-dev` exists) and push it to the
   local registry.
3. `helm upgrade --install po ./charts/po -n po-smoke` with the image
   override — exercises the chart's `pre-install` pool-register hook.
4. `seed-credentials.sh` creates `anthropic-api-key` (apikey mode) or
   `claude-oauth` (oauth mode) Secret out-of-band.
5. `seed-rig.sh` builds a throwaway target git repo
   (`./.smoke/target-<utc>.git` bare + working clone), runs `bd init`,
   creates one trivial open bead, and `kubectl cp`s the working tree
   onto the chart's rig PVC at `/rig/smoke-target` via a busybox
   sleeper pod.
6. A one-shot `po-smoke-trigger-<ts>` pod runs
   `po run software-dev-full --issue-id <id> --rig smoke --rig-path /rig/smoke-target`.
7. `assert-success.sh` polls until the bead reports `closed` and the
   smoke-target git tree has at least one new commit since
   `lib.sh::capture_start_ts`.
8. `teardown-kind.sh` (via EXIT trap) deletes the kind cluster and
   the registry container.

### Hetzner upgrade path

```bash
export HCLOUD_TOKEN=<...> ANTHROPIC_API_KEY=sk-…
export HCLOUD_SSH_KEY=my-ssh-key       # key already registered with hcloud
SMOKE_DRIVER=hetzner ./scripts/cloud-smoke/run-smoke.sh
```

Differences from kind:

- `provision-hetzner.sh` provisions one CX22 with cloud-init that
  installs k3s (no traefik), waits for `/etc/rancher/k3s/k3s.yaml`,
  rewrites the loopback API endpoint to the public IP, and writes
  the kubeconfig to `./.smoke/kubeconfig`.
- The worker image is built locally, `docker save`d to a tarball,
  `scp`'d to the node, and `k3s ctr images import`ed — no public
  registry, no imagePullSecrets.
- `teardown-hetzner.sh` deletes the server plus any volumes /
  load-balancers whose names are prefixed `po-smoke-*`. Idempotent.

## Env knobs

| Var | Default | Purpose |
|---|---|---|
| `SMOKE_DRIVER` | `kind` | `kind` \| `hetzner` |
| `SMOKE_AUTH` | `apikey` | `apikey` (uses `$ANTHROPIC_API_KEY`) \| `oauth` (uses `~/.claude/.credentials.json`) |
| `SMOKE_DRY` | `0` | When `1`, prints commands but skips destructive ops |
| `SMOKE_KEEP` | `0` | When `1`, skips tear-down on success (debugging) |
| `SMOKE_NAMESPACE` | `po-smoke` | k8s namespace |
| `SMOKE_RELEASE` | `po` | helm release name (also drives chart-rendered names) |
| `SMOKE_CLUSTER` | `po-smoke` | kind cluster name |
| `SMOKE_REGISTRY_PORT` | `5001` | host port for the local registry |
| `SMOKE_PACK_PATH` | `packs/po-formulas-software-dev` | absolute / relative path to a pack to bake into the image |
| `SMOKE_TIMEOUT_MIN` | `20` | exit-gate poll budget |
| `SMOKE_ISSUE_ID` | `smoke-1` | overridden by `seed-rig.sh` to the freshly-created bead's id |
| `HCLOUD_SSH_KEY` | — | required for Hetzner driver |
| `HCLOUD_SERVER_TYPE` | `cx22` | Hetzner server type |
| `HCLOUD_LOCATION` | `fsn1` | Hetzner location |

CLI flag passthroughs on `run-smoke.sh`: `--dry-run`, `--keep`,
`--kind`, `--hetzner`, `--apikey`, `--oauth`.

## Exit-gate semantics

`assert-success.sh` polls every 30 s up to `SMOKE_TIMEOUT_MIN`
minutes. Both conditions must hold for a green exit:

1. `bd show <issue-id> --json` (executed inside a worker pod with
   the rig PVC mounted at `/rig`) reports `status == "closed"`.
2. `git -C /rig/smoke-target log --since=<smoke-start>` lists at
   least one commit (a stamp captured by `lib.sh::capture_start_ts`).

On success the harness prints:

```
=== smoke OK: bead <issue-id> closed, commit <sha> on target ===
```

Anything else exits non-zero, which the orchestrator's trap turns
into tear-down + a non-zero process exit.

## Tear-down guarantees

- `trap cleanup EXIT INT TERM` in `run-smoke.sh` runs the matching
  `teardown-${SMOKE_DRIVER}.sh` on every exit path (success, failure,
  Ctrl-C). Set `SMOKE_KEEP=1` to skip on success only — failures
  always tear down.
- Both teardown scripts are **idempotent** with explicit existence
  checks (no blanket `|| true`). Re-running on a clean state exits 0.
- The Hetzner teardown filters volumes / LBs by the `po-smoke-` name
  prefix so user-owned resources are never touched.

## Known limitations

- **`git push` back to host bare repo** — the kind driver currently
  runs in *offline-target mode*: the worker commits into the rig's
  cloned working tree, and the exit-gate inspects that tree directly.
  Pushing back into `./.smoke/target-<utc>.git` requires either a
  hostPath bind (not in the chart) or a sidecar `git-daemon`. Tracked
  separately; the assert-success path is unaffected.
- **PVC seeding via `kubectl cp`** — fast but synchronous on the
  busybox sleeper. Not suitable for >50 MiB rigs; if your smoke
  outgrows that, swap to an init-container clone (deferred — see
  `engdocs/work-pools.md` "Rig-state strategy").
- **Hetzner image push** — uses `k3s ctr images import` over SSH. No
  public registry, no imagePullSecrets, but per-run the image
  must be re-imported (no caching across smoke invocations).
- **OAuth secret material** — `seed-credentials.sh` uses
  `kubectl create secret … --from-file=` so credential bytes never
  appear in `ps`/shell-history. The script disables `set -x` locally
  and never echoes the file's contents. Still: don't run the smoke
  on a shared CI host without scoped service accounts.
- **Concurrent smokes** — the throwaway repo path includes a UTC
  timestamp (`target-<utc>.git`) so two parallel runs don't clobber.
  But the kind cluster name (`po-smoke`) is fixed; override
  `SMOKE_CLUSTER` for parallelism.

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `helm install` hangs on pool-register | `kubectl describe job/<release>-pool-register` shows ImagePullBackOff | The mirror isn't wired — confirm `docker network inspect kind` lists the registry container. Re-run `provision-kind.sh`; it's idempotent. |
| `seed-credentials.sh` fails fast | Required env / file missing | `apikey` mode needs `$ANTHROPIC_API_KEY`; `oauth` mode needs `~/.claude/.credentials.json`. No silent fallback. |
| Trigger pod stuck in `Pending` | RWO PVC already bound to worker on a different node | This shouldn't happen on single-node kind. On Hetzner with replicas>1, set `worker.replicaCount=1` (the chart default). |
| `assert-success.sh` times out | Real flow took longer than 20 m | Bump `SMOKE_TIMEOUT_MIN`; inspect `kubectl logs deploy/po-worker` for the failed step. |
| Trigger pod never sees the bead | Rig was seeded into a different mount path | The chart mounts the rig at `/rig`; `seed-rig.sh` writes to `/rig/smoke-target/`; trigger uses `--rig-path /rig/smoke-target`. Mismatches surface as `bd show` errors. |

## Manual debugging escape hatches

- `SMOKE_KEEP=1 ./scripts/cloud-smoke/run-smoke.sh` — preserve
  cluster on success for `kubectl exec` poking.
- `kubectl -n po-smoke port-forward svc/po-prefect-server 4200:4200` —
  reach the Prefect UI in the kind cluster.
- `kubectl -n po-smoke exec deploy/po-worker -- bash` — drop into
  the worker pod with the rig PVC mounted at `/rig`.

[`prefect-orchestration-tyf.5`]: https://example.invalid/  <!-- bead id only -->
