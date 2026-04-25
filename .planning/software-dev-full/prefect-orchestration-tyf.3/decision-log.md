# Decision log — prefect-orchestration-tyf.3

- **Decision**: On-disk credentials file wins over `CLAUDE_CREDENTIALS` env in `docker/entrypoint.sh`.
  **Why**: Plan §3 (Entrypoint correctness). Without on-disk-first ordering, every restart clobbers the freshly-refreshed token with the (stale) Secret payload, defeating PVC persistence (option a). Generalizes the prior `elif` branch that already handled the docker-compose bind-mount case.
  **Alternatives considered**: mtime/content-based merge (rejected as over-engineered — the simpler "non-empty file wins" rule is sufficient for tyf.3's scope; rotation is documented as a separate workflow).

- **Decision**: Implement option (a) PVC as opt-in only; do not change the default Deployment env block.
  **Why**: Plan "Decision" section + risk analysis. Default k8s path is API-key for prod; OAuth is dev/non-prod ergonomics. Keeping option (a) opt-in avoids forcing all users into RWO + replicas:1.
  **Alternatives considered**: making OAuth+PVC the default (rejected — would silently force scale-down for API-key users on `kubectl apply`).

- **Decision**: Reject option (b) (sidecar Secret-syncback) outright; document the rejection.
  **Why**: Requires `secrets/update` RBAC in-namespace; blast radius is too high for a dev-ergonomics fix when option (a) achieves the same persistence with kubelet primitives.
  **Alternatives considered**: scoped RBAC + audited sidecar (deferred — only revisit if multi-pod OAuth scale-out is required).

- **Decision**: Export `PO_AUTH_SOURCE` (`disk` | `env` | empty) in addition to `PO_AUTH_MODE`.
  **Why**: Lets the manual-smoke runbook in `engdocs/auth.md` distinguish first-boot from persistence-restored boots without parsing logs. Also enables a tighter test assertion that the on-disk branch was actually taken.
  **Alternatives considered**: log-only (rejected — env var is structured and survives stdout buffering for downstream introspection).

- **Decision**: Document the manual kubectl-restart smoke in `engdocs/auth.md` rather than automating it.
  **Why**: Plan "Test plan". A real kubelet restart cycle requires a live cluster; gating tyf.3 on cloud infra would block the ticket. The runbook is wired into tyf.5 (cloud smoke).
  **Alternatives considered**: kind/k3d-based integration test (deferred — adds a heavyweight dependency to the unit suite for a one-time AC).
