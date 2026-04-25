# Plan: prefect-orchestration-tyf.1 — OAuth credential injection

Wire OAuth credential injection into the existing `po-worker` image entrypoint
so workers can authenticate via Claude.ai subscription (`CLAUDE_CREDENTIALS`)
in addition to the current `ANTHROPIC_API_KEY` path. The image already runs as
non-root user `coder`, so the credentials target is `$HOME/.claude/.credentials.json`
(== `/home/coder/.claude/.credentials.json`), **not** `/root/.claude/...` as the
issue text suggests — the triage already flagged this; we'll resolve via `$HOME`.

## Affected files

- `docker/entrypoint.sh` — add OAuth-mode branch ahead of the existing
  API-key bootstrap; on `CLAUDE_CREDENTIALS` set, materialize the file at
  `$HOME/.claude/.credentials.json` (mode 0600), `unset ANTHROPIC_API_KEY`,
  and skip the `customApiKeyResponses` block in `~/.claude.json`.
- `Dockerfile` — header comment block updated to describe the dual-auth
  behavior (currently only describes `ANTHROPIC_API_KEY`). No layer changes
  expected.
- `docker-compose.yml` — uncomment the bind mount for
  `${HOME}/.claude/.credentials.json:/home/coder/.claude/.credentials.json:ro`
  with a comment noting it's the OAuth alternative; relax the
  `ANTHROPIC_API_KEY` requirement so compose works in either mode.
- `k8s/po-worker-deployment.yaml` — add a commented-out `claude-oauth`
  Secret volume + `CLAUDE_CREDENTIALS` env block (from a Secret) showing
  both injection styles. Keep `ANTHROPIC_API_KEY` block as the default.
- `k8s/claude-oauth.example.yaml` (new) — sibling of `anthropic-api-key.example.yaml`
  documenting `kubectl create secret generic claude-oauth --from-file=credentials.json=$HOME/.claude/.credentials.json`.
- `README.md` — new short "Auth modes" subsection covering: (a) OAuth via
  `CLAUDE_CREDENTIALS` env, (b) `ANTHROPIC_API_KEY`, (c) local compose
  bind-mount, (d) k8s Secret recipes for both. Cross-link from existing
  Docker section.
- `tests/test_entrypoint.sh` (new shell test) **or** `tests/test_docker_entrypoint.py`
  (pytest that shells out): assert that running the entrypoint with
  `CLAUDE_CREDENTIALS={...}` writes the file, sets perms 0600, unsets
  `ANTHROPIC_API_KEY`; and that with only `ANTHROPIC_API_KEY` set, the
  credentials file is absent and `~/.claude.json` contains the API-key
  approval block. Run via `bash` against a tmp `HOME` — no Docker needed.

## Approach

Add a guarded OAuth branch at the top of `docker/entrypoint.sh`:

```bash
if [[ -n "${CLAUDE_CREDENTIALS:-}" ]]; then
  install -d -m 700 "$HOME/.claude"
  umask 077
  printf '%s' "$CLAUDE_CREDENTIALS" > "$HOME/.claude/.credentials.json"
  chmod 600 "$HOME/.claude/.credentials.json"
  unset CLAUDE_CREDENTIALS              # don't leak via /proc/<pid>/environ
  unset ANTHROPIC_API_KEY               # SDK prefers OAuth only when key is absent
  PO_AUTH_MODE=oauth
else
  PO_AUTH_MODE=apikey
fi
```

Then split the existing `ANTHROPIC_API_KEY` enforcement so it only fires
when `PO_AUTH_MODE=apikey` AND `PO_BACKEND` isn't `stub`. The existing
`~/.claude.json` write keeps running, but in OAuth mode we drop the
`customApiKeyResponses` block (Claude Code reads creds from the credentials
file in that mode) — emit a minimal config that still trusts `/workspace`
and `/rig`. Avoid `set -x`; never echo the env var. Use `printf '%s'`
not `echo` so JSON braces don't get interpreted.

Precedence: **OAuth wins if `CLAUDE_CREDENTIALS` is set**, matching the
global rule (subscription preferred for non-prod; API key is the fallback).
This intentionally lets a misconfigured pod fail loudly rather than silently
fall through to a wrong auth mode.

Keep `tyf.3` (token refresh persistence) unblocked: writing to
`$HOME/.claude/.credentials.json` from a Secret works equally well when
`tyf.3` swaps that path for a PVC-backed mount — the entrypoint just
won't overwrite a pre-existing file in OAuth mode if we add an "exists
& non-empty" guard. Document this as a TODO comment but don't implement
the guard yet (out of scope).

## Acceptance criteria (verbatim)

- Worker image has /app/start.sh (or entrypoint) that materializes CLAUDE_CREDENTIALS to /root/.claude/.credentials.json before exec
- Falls back to ANTHROPIC_API_KEY auth if CLAUDE_CREDENTIALS unset (use rclaude's ~/.claude.json bootstrap pattern)
- README documents the k8s Secret recipe (kubectl create secret generic claude-oauth --from-file=credentials.json=/home/ryan-24/.claude/.credentials.json) and the env-var alternative
- Local docker-compose path: bind-mount ~/.claude/.credentials.json read-only (no env-var copy needed for local)

> Path note: per triage, target is `$HOME/.claude/.credentials.json`
> (`/home/coder/...`), not `/root/...`, because the worker image runs as
> the non-root `coder` user. The AC's intent — "materialize creds before
> exec" — is preserved; only the literal path is corrected.

## Verification strategy

| AC | Verification |
|---|---|
| AC1 (entrypoint materializes creds) | Shell-level test: invoke `docker/entrypoint.sh /bin/true` with `HOME=$tmp`, `CLAUDE_CREDENTIALS='{"k":"v"}'`. Assert `cat $tmp/.claude/.credentials.json == '{"k":"v"}'` and `stat -c '%a' == '600'`. |
| AC2 (fallback to API key) | Shell-level test: same harness, no `CLAUDE_CREDENTIALS`, `ANTHROPIC_API_KEY=sk-test1234567890abcdef`. Assert `$tmp/.claude/.credentials.json` does not exist; `$tmp/.claude.json` exists; contains `customApiKeyResponses` and the last 20 chars of the key. |
| AC3 (README documents both) | Grep README post-edit for `claude-oauth`, `CLAUDE_CREDENTIALS`, and `ANTHROPIC_API_KEY` in a single "Auth modes" section. Plus visual review. |
| AC4 (compose bind-mount) | Inspect `docker-compose.yml` — bind-mount line uncommented and target path `/home/coder/.claude/.credentials.json`. Manual smoke optional: `unset ANTHROPIC_API_KEY; docker compose run --rm client po doctor` succeeds when host has valid creds. |

## Test plan

- **Unit/shell**: new `tests/test_docker_entrypoint.py` (pytest, shells out to `bash docker/entrypoint.sh /bin/echo ok` under a tmp `HOME`). Three cases: OAuth mode, API-key mode, neither (should `exit 64` when `PO_BACKEND=cli`). No Docker required → runs in CI.
- **e2e (optional, gated)**: `scripts/smoke-compose.sh` already exists with `PO_BACKEND=stub`. Add an OAuth-mode variant that exports `CLAUDE_CREDENTIALS="$(cat ~/.claude/.credentials.json)"` only when the file exists; skip otherwise. Don't make this required.
- **Playwright**: N/A (no UI).

## Risks

- **Path discrepancy with the AC text**: AC says `/root/...`, image uses `/home/coder/...`. We follow the triage and use `$HOME`. Document this in the decision log so the critic doesn't bounce the build.
- **API contract**: none — purely runtime/container behavior. No public Python API or formula signature changes.
- **Breaking consumers**: existing `ANTHROPIC_API_KEY`-only deployments must keep working unchanged. Verified by AC2 and by leaving the env block in `po-worker-deployment.yaml` intact.
- **Secret leakage**: avoid `set -x`; never echo `$CLAUDE_CREDENTIALS`; `unset` after writing. Add a comment in the entrypoint pinning this requirement so future edits don't regress.
- **Compose UX regression**: the worker service currently passes through `ANTHROPIC_API_KEY` from host env. If we tighten that path (e.g., refuse empty), compose users running `up` without a key get a worse error. Mitigation: leave compose default behavior unchanged; only the entrypoint logic gates on auth mode.
- **k8s example file drift**: a new `claude-oauth.example.yaml` is easy to forget when secrets rotate. Mark as `.example.yaml` (matches existing convention).
- **tyf.3 forward-compat**: token refresh write-back will likely need the credentials file to be writable / persisted. We chmod 600 but keep the file in `$HOME/.claude/` (writable by `coder`) so refresh won't `EACCES`. Don't make the file read-only.
