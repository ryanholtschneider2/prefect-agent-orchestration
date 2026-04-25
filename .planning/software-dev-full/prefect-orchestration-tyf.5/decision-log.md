# Decision log — prefect-orchestration-tyf.5

## Build iter 1

- **Decision**: Made `kind` the default smoke driver; ship `hetzner`
  as a sibling driver instead of replacing it.
  **Why**: Triage explicitly endorsed the kind-first trade-off (cheap,
  scriptable, deterministic, no cloud creds gate). The Hetzner script
  remains so the operator can flip a single env var when they want
  real cloud network behaviour. Plan §"Cluster choice".
  **Alternatives considered**: Hetzner-only (rejected — costs money on
  every CI run; needs HCLOUD_TOKEN); kind-only (rejected — triage
  explicitly asked for the cloud upgrade path documented).

- **Decision**: Smoke runs in *offline-target mode* — worker commits
  into the rig PVC's working tree, not back into the host bare repo.
  **Why**: Pushing back to a host bare repo from inside a kind /
  Hetzner pod requires either a hostPath bind or a sidecar git-daemon,
  neither of which the chart ships. The exit-gate's "commit landed"
  check works equivalently against `/rig/smoke-target/.git` inside the
  pod via `kubectl exec`. Documented as a known limitation in
  `engdocs/cloud-smoke.md`.
  **Alternatives considered**: hostPath bind (chart change, out of
  scope); sidecar git-daemon (premature for a smoke); throwaway GitHub
  repo (requires an `SMOKE_GITHUB_REPO` PAT — adds CI surface).

- **Decision**: Hetzner image distribution via `docker save` + `scp`
  + `k3s ctr images import` rather than a public/private registry.
  **Why**: Avoids an extra moving part (registry credentials,
  imagePullSecrets, throwaway DNS). The k3s containerd accepts direct
  imports cleanly, matching the triage "Image source" risk note.
  **Alternatives considered**: Hetzner Container Registry (adds
  per-account setup); ghcr.io (needs PAT + secret in the chart).

- **Decision**: PVC seeding via a throwaway busybox sleeper +
  `kubectl cp`, not an init-container clone or a hostPath bind.
  **Why**: Simplest mechanism that works across both drivers and
  doesn't require chart changes. Sleeper is short-lived and explicitly
  deleted after the cp completes. `engdocs/cloud-smoke.md` flags the
  >50 MiB cliff and points to `engdocs/work-pools.md` "Rig-state
  strategy" for the deferred init-container path.
  **Alternatives considered**: init-container clone (requires git
  remote — this repo has none); chart-level hostPath bind (cross-cuts
  tyf.4's chart shape).

- **Decision**: `seed-credentials.sh` refuses to run when the
  requested credential source is missing — no silent fallback.
  **Why**: User-global CLAUDE.md "Silent fallbacks in distributed
  pipelines" — a dispatcher must fail loudly when prerequisites are
  absent. Validates `$ANTHROPIC_API_KEY` (apikey mode) or
  `~/.claude/.credentials.json` (oauth mode) up front; uses
  `--from-literal=` / `--from-file=` so credential bytes never enter
  shell history or `ps`.

- **Decision**: Tear-down lives in an EXIT/INT/TERM trap inside
  `run-smoke.sh`; teardown scripts are idempotent with explicit
  existence checks (no blanket `|| true`).
  **Why**: Triage "Tear-down idempotence" — half-failed provision
  must still tear down cleanly. `kind get clusters | grep -qx`,
  `docker inspect`, and `hcloud server list | awk` all gate destructive
  ops on object presence. Hetzner tear-down filters volumes/LBs by
  the `po-smoke-` name prefix so user-owned resources are untouched.

- **Decision**: Test layout — cheap parse + shellcheck pytest in the
  unit suite, dry-run e2e enabled by default, full e2e gated on
  `RUN_CLOUD_SMOKE=1`.
  **Why**: Default CI must stay fast. The dry-run e2e exercises the
  orchestrator phases without invoking docker/kind, so regressions in
  argument parsing surface in routine runs. The full smoke remains a
  manual operator gate.

- **Decision**: Did NOT extend `tests/test_helm_chart.py` —
  `test_helm_template_oauth_mode_emits_credentials_env` already
  asserts `claude-oauth` is referenced when `auth.mode=oauth` (lines
  85–90). The plan's "extend with `grep -q claude-oauth`" assertion
  is already covered.
  **Why**: No-op edit would just be churn. Verified before skipping.
