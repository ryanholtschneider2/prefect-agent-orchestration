# Plan: prefect-orchestration-tyf.5 — Cloud smoke test

## Goal

Ship a self-contained, scripted smoke that provisions a real (small)
cluster, helm-installs `charts/po`, runs one trivial bead through
`software-dev-full`, asserts the bead auto-closed and the commit
landed on a throwaway target repo, then tears the cluster back down.
Output is **scripts + engdocs**, not a new core feature.

## Cluster choice (justified)

**Primary path: kind** (free, scriptable, deterministic in CI/local
dev). **Documented upgrade path: Hetzner k3s** (real cloud networking
+ image pull, ~€0.05/hr CX22, runs the same scripts via
`KIND=0 HCLOUD_TOKEN=… ./provision.sh`). Triage flags Hetzner as
ideal but kind-first is the cheapest credible smoke that doesn't
gate on cloud creds — it still exercises the full chart, pool-register
hook, PVC mount, OAuth/API-key Secret, image pull (from a local
registry), and end-to-end `bd` round-trip.

The two paths share one Helm install path; only `provision.sh` /
`teardown.sh` differ. We commit both but make kind the default.

## Affected files

New (under `scripts/cloud-smoke/`):

- `scripts/cloud-smoke/provision-kind.sh` — `kind create cluster`,
  spin up a local registry container, wire the registry to kind, build
  + push `po-worker:smoke` with the `software-dev` pack baked in.
- `scripts/cloud-smoke/provision-hetzner.sh` — `hcloud server create`
  → cloud-init installs k3s + helm + kubectl, writes a kubeconfig to
  `./.smoke/kubeconfig`, builds + pushes the worker image to a
  throwaway registry (or `k3s ctr images import` via SSH).
- `scripts/cloud-smoke/seed-rig.sh` — creates a throwaway target git
  repo (local bare repo + clone, or a tmp GitHub repo if
  `SMOKE_GITHUB_REPO` is set), runs `bd init` inside it, creates one
  trivial open bead (`add comment to README`), then `kubectl cp`s the
  rig directory onto the chart's `po-rig` PVC via a sleeper pod.
- `scripts/cloud-smoke/seed-credentials.sh` — pulls
  `~/.claude/.credentials.json` (OAuth) **or** `$ANTHROPIC_API_KEY`
  from the host and creates the matching k8s Secret. Refuses to run
  if neither source is present. Never echoes credential bytes.
- `scripts/cloud-smoke/run-smoke.sh` — top-level orchestrator:
  `provision → install chart → seed rig + creds → trigger run →
  assert exit gate → echo summary`. Honors `SMOKE_DRIVER=kind|hetzner`
  (default kind) and `SMOKE_AUTH=apikey|oauth` (default apikey).
- `scripts/cloud-smoke/assert-success.sh` — exit-gate script: polls
  `bd show <id>` (via `kubectl exec` on the worker) until status is
  `closed` or timeout, then `git log` against the throwaway target
  repo to confirm the commit landed. Fails loudly with a non-zero
  exit code so the orchestrator surfaces it.
- `scripts/cloud-smoke/teardown-kind.sh` — `kind delete cluster` +
  `docker rm -f` the registry container. Idempotent.
- `scripts/cloud-smoke/teardown-hetzner.sh` — `hcloud server delete`
  + LB / volume cleanup. Idempotent (existence checks before delete,
  no blanket `|| true`).
- `scripts/cloud-smoke/lib.sh` — shared helpers: `require_cmd`,
  `wait_for_pool_register`, `kubectl_cp_rig`, `cleanup_on_exit`
  trap (so `Ctrl-C` mid-provision doesn't strand resources).

New docs:

- `engdocs/cloud-smoke.md` — operator's guide. Pre-reqs (`kind`,
  `helm`, `kubectl`, `docker` for kind; `hcloud` for Hetzner),
  one-liner happy paths, exit-gate semantics, tear-down guarantees,
  `SMOKE_*` env-var matrix, troubleshooting.

Touches:

- `engdocs/work-pools.md` — append a "Cloud smoke" section linking to
  `engdocs/cloud-smoke.md` so operators discover the script from the
  existing helm walkthrough.
- `README.md` — one-line pointer under "Smoke / verification" to the
  new doc.
- `charts/po/templates/NOTES.txt` — if missing, append a hint that
  `engdocs/cloud-smoke.md` ships an end-to-end installer/runner.

No core or pack code changes are planned. If the smoke uncovers a
real bug we'll either patch it inline (single-line fixes) or open a
follow-up bead — keep this issue's scope to scripts + docs.

## Approach

### Provisioning (kind path, default)

1. `kind create cluster --name po-smoke --config -` (single-node, with
   `extraPortMappings` so the local registry survives Docker network
   churn).
2. Run a `registry:2` container on host net at `127.0.0.1:5001`,
   connect it to the kind network, document it in the cluster's
   `containerd` config so kind nodes pull from it.
3. `docker build -t 127.0.0.1:5001/po-worker:smoke
       --build-context pack=../software-dev/po-formulas .` then push.
4. `kubectl create namespace po-smoke`.

### Helm install

```bash
helm install po ./charts/po -n po-smoke \
    --set worker.image.repository=127.0.0.1:5001/po-worker \
    --set worker.image.tag=smoke \
    --set "auth.mode=$SMOKE_AUTH"     # apikey | oauth
helm test po -n po-smoke              # exercises pool-register hook
```

The chart's `pre-install` hook job creates the `po` work pool;
`helm test` confirms wiring; `kubectl rollout status
deployment/po-worker` confirms the worker comes up.

### Seeding the rig + credentials

- **Rig delivery**: PVC seed via `kubectl cp`. `seed-rig.sh` runs a
  throwaway `busybox` pod with the `po-rig` PVC mounted at `/rig`,
  `kubectl cp`s a freshly initialized throwaway target repo (with
  `.beads/` + one open bead) into `/rig`, then deletes the sleeper.
  The throwaway repo is a local bare repo (`./.smoke/target.git`)
  cloned into the rig — the worker will `git push` into the bare repo
  to prove the commit landed without needing a public remote.
- **Credentials**: `seed-credentials.sh` runs **after** `helm install`
  so the chart's Secret references resolve, but before any flow
  triggers. Defaults to `apikey` mode reading `$ANTHROPIC_API_KEY`;
  flip to `oauth` to mount `~/.claude/.credentials.json`. Both modes
  use `kubectl create secret generic … --from-literal/--from-file`
  (out-of-band, matches the chart's `auth.*.createSecret=false`
  default). The script refuses to run if the requested credential
  source is absent — no silent fallback.

### Triggering the run

`run-smoke.sh` shells into a `client`-style one-shot:

```bash
kubectl -n po-smoke run po-trigger \
    --rm -i --restart=Never \
    --image=127.0.0.1:5001/po-worker:smoke \
    --env=PREFECT_API_URL=http://po-prefect-server:4200/api \
    --env=PO_BACKEND=cli \
    --command -- \
    po run software-dev-full \
        --issue-id "$SMOKE_ISSUE_ID" \
        --rig smoke --rig-path /rig
```

(or `prefect deployment run` if a deployment is pre-applied — the
direct `po run` path is simpler and matches the smoke-compose
precedent.)

### Exit gate

`assert-success.sh` polls (max 20 min, 30 s tick):

1. `kubectl -n po-smoke exec deploy/po-worker -- bd --db /rig/.beads/… show $SMOKE_ISSUE_ID --json` → require `status == "closed"`.
2. `git -C ./.smoke/target.git log --oneline` → require ≥ 1 new
   commit since the smoke started (timestamp captured in `lib.sh`).

Both must pass for `run-smoke.sh` to exit 0.

### Tear-down

A `trap cleanup EXIT` in the orchestrator calls
`teardown-{kind,hetzner}.sh` so a `Ctrl-C` or assertion failure still
deletes the cluster. Tear-down is **idempotent** — each step is
guarded by an existence check; no blanket `|| true`. Matches the
triage requirement that tear-down works after a partial provision.

## Acceptance criteria (verbatim)

> Smoke runs end-to-end on a real cluster; bead closes; tear-down works

Plus the issue body's three bullets:

> - Documented script that provisions a tiny cluster, applies the helm chart, runs the smoke
> - One full software-dev-full run completes on the cluster, bead auto-closes
> - Tear-down script reverses provisioning so we don't burn money

## Verification strategy

| AC | Concrete check |
|---|---|
| Documented script that provisions + installs + runs | `engdocs/cloud-smoke.md` exists; `bash -n scripts/cloud-smoke/*.sh` parses; `shellcheck` clean; `run-smoke.sh --dry-run` (env `SMOKE_DRY=1`) prints the planned action graph without invoking `kind`/`docker`/`helm`. |
| One full `software-dev-full` run completes; bead auto-closes | `assert-success.sh` polls `bd show` and asserts `status == "closed"`. Exit code propagates to `run-smoke.sh`. A successful run prints `=== smoke OK: bead <id> closed, commit <sha> on target ===`. |
| Tear-down reverses provisioning | `teardown-kind.sh` ⇒ `kind get clusters` no longer lists `po-smoke` AND `docker ps -aq -f name=po-smoke-registry` is empty. `teardown-hetzner.sh` ⇒ `hcloud server list -o noheader` filters to no `po-smoke-*`. Both are idempotent: re-running on an already-clean state exits 0. |

## Test plan

- **Unit (pytest)**: extend `tests/test_helm_chart.py` (already
  exercises `helm lint charts/po`) with a `helm template ./charts/po
  --set auth.mode=oauth | grep -q claude-oauth` assertion to catch
  values regressions the smoke depends on. Cheap, runs in CI.
- **Shell parse / lint**: add a tiny pytest that walks
  `scripts/cloud-smoke/*.sh` and asserts `bash -n` parses + (if
  `shellcheck` is on PATH) `shellcheck` is clean. Skips gracefully
  when `shellcheck` is absent so it doesn't gate normal CI.
- **e2e (subprocess)**: gate behind `RUN_CLOUD_SMOKE=1` in
  `tests/e2e/test_cloud_smoke.py` — too heavy for the default suite
  (spins up kind, pulls images, runs Claude). Test invokes
  `scripts/cloud-smoke/run-smoke.sh --dry-run` to confirm the
  orchestrator wiring without actually provisioning. The real run
  remains a manual operator gate.
- **Playwright**: N/A. No new UI; Prefect's bundled UI is unchanged.

## Risks

- **Kind-only smoke is not "real cloud"** — accepted: kind is the
  default for cost reasons; Hetzner script ships alongside as the
  documented upgrade path. Triage explicitly endorsed this trade-off.
- **OAuth credentials handling** — script reads
  `~/.claude/.credentials.json` from the host and pipes it into
  `kubectl create secret`. Risk: leaking via shell history /
  process listing. Mitigation: `--from-file=` (no command-line
  bytes), `set +x` around credential ops, never echoed.
- **Throwaway repo collision** — naming the bare repo
  `.smoke/target-<utc>.git` so concurrent runs don't clobber.
- **PVC seed timing** — `kubectl cp` requires a running pod with the
  PVC already bound. `seed-rig.sh` waits for `kubectl wait
  --for=condition=Ready` on the sleeper before copying.
- **Image pull on Hetzner** — k3s + a remote private registry needs
  imagePullSecrets. The Hetzner provisioner sidesteps via
  `k3s ctr images import` over SSH (push the local tarball directly).
  No registry credentials in the chart.
- **Stranded LBs** — kind has none; Hetzner only if we accidentally
  enable `ingress.enabled=true`. The smoke leaves Ingress off
  (Prefect UI accessed via `kubectl port-forward`).
- **No git remote on this repo** — the smoke target is a separate
  throwaway repo (local bare or `SMOKE_GITHUB_REPO` env-var). We do
  not push smoke artifacts back into `prefect-orchestration`.
- **API contract** — none. Pure scripting + docs; no public Python /
  CLI surface change. No migration. No breaking consumer impact.
