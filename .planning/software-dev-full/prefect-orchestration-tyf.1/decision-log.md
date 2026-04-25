# Decision log — prefect-orchestration-tyf.1

## Build iter 1

- **Decision**: Target path is `$HOME/.claude/.credentials.json` (resolves
  to `/home/coder/.claude/.credentials.json` in the image), not
  `/root/.claude/.credentials.json` as the AC literal suggests.
  **Why**: The image runs as the non-root `coder` user (Dockerfile L122
  `USER coder`). Claude Code refuses `--dangerously-skip-permissions`
  as root, so we cannot run as root just to satisfy the literal path.
  Triage already flagged this; AC intent ("materialize creds before
  exec") is preserved.
  **Alternatives considered**: Switching the container to root just
  to match the AC text — rejected as it breaks Claude Code's TUI
  refusal behavior and contradicts the existing image design.

- **Decision**: Three-way OAuth detection in the entrypoint:
  (1) `CLAUDE_CREDENTIALS` env set → write file + 0600 + scrub key,
  (2) elif file already exists at the target path (bind-mount case) →
  flip to OAuth mode, leave the file alone, scrub key,
  (3) else → API-key bootstrap.
  **Why**: AC4 says "Local docker-compose path: bind-mount
  ~/.claude/.credentials.json read-only (no env-var copy needed for
  local)". Without branch (2), a bind-mount worker would still go
  down the API-key path because `CLAUDE_CREDENTIALS` is unset, and
  the SDK would then either fail (no key) or — worse — silently use
  a stale key if one were lying around. Branch (2) honours the
  bind-mount intent without needing the host to round-trip the file
  through an env var.
  **Alternatives considered**: Requiring the env var even for the
  bind-mount case — rejected because it makes the local UX
  significantly worse (the user would have to `export CLAUDE_CREDENTIALS=$(cat ~/...)`).

- **Decision**: OAuth wins when both `CLAUDE_CREDENTIALS` and
  `ANTHROPIC_API_KEY` are set, with `unset ANTHROPIC_API_KEY` before
  exec.
  **Why**: Global CLAUDE.md rule — "OAuth via subscription is the
  global rule for non-prod / dev workers; ANTHROPIC_API_KEY is the
  production fallback". And the SDK silently prefers
  `ANTHROPIC_API_KEY` over OAuth if both are present, so an explicit
  `unset` is required to make OAuth actually take effect.
  **Alternatives considered**: API-key wins (the historical default)
  — rejected; contradicts the global rule. Throwing on conflict —
  rejected; users may not control both env vars (e.g. shell-wide
  `ANTHROPIC_API_KEY` plus per-pod `CLAUDE_CREDENTIALS` Secret).

- **Decision**: In OAuth mode, the bootstrapped `~/.claude.json`
  drops the `customApiKeyResponses` block.
  **Why**: That block tells Claude Code to accept a specific API-key
  suffix without prompting. In OAuth mode there's no API key to
  approve, and leaving a stale block would suggest the API-key
  pathway is in use. Keeping the `hasCompletedOnboarding` /
  `bypassPermissionsModeAccepted` / `projects` keys is enough for
  Claude Code to start without a TTY.
  **Alternatives considered**: Always emit both blocks regardless of
  mode — rejected as it introduces dead config that drifts from the
  actual auth method.

- **Decision**: Did NOT modify the `Dockerfile` (only a header
  comment update was planned).
  **Why**: The reservation request reported `Dockerfile` held by
  another agent (`RoseMeadow`, exclusive). The comment update is
  cosmetic and does not block the AC; deferring avoids racing the
  other worker. README + entrypoint comments already cover the new
  auth modes adequately.
  **Alternatives considered**: Wait 60s for the reservation to
  expire and edit anyway — rejected; comment-only changes aren't
  worth the coordination overhead, and a future build iter or
  rebase can pick it up.

- **Decision**: Tests are pure-bash invocations under a tmp `HOME`
  rather than docker-build / docker-run integration tests.
  **Why**: The entrypoint is shell logic, not container plumbing.
  Pure-bash tests run in milliseconds in CI without a Docker daemon,
  and they verify exactly the AC behavior (file written? mode 0600?
  API-key scrubbed?). Container-level tests would add Docker as a
  test dependency for negligible coverage gain.
  **Alternatives considered**: Container integration test —
  deferred to manual `scripts/smoke-compose.sh` (already exists for
  stub mode); a future bead can add an OAuth-mode smoke variant.

- **Decision**: `compose` services now pass through `CLAUDE_CREDENTIALS`
  alongside `ANTHROPIC_API_KEY`, but the bind-mount line stays
  commented by default.
  **Why**: Two reasons. (a) Active bind-mount when host file is
  absent makes Docker create an empty file (or error on some Docker
  versions) — surprising to first-time users. (b) The README "Auth
  modes" section explicitly documents how to enable it, so the
  default-off + commented-line shape matches the existing
  conservative pattern (e.g. the `additional_contexts` block above
  it).
  **Alternatives considered**: Uncomment by default — rejected per
  (a). Drop the bind-mount line entirely — rejected; AC4 calls it
  out as the local path.
