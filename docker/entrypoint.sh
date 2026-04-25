#!/usr/bin/env bash
# PO worker entrypoint — bootstraps Claude Code so it doesn't hang on
# the trust dialog or onboarding flow inside a container, then `exec`s
# the supplied command.
#
# Auth modes (precedence: OAuth wins if both are set):
#   1. CLAUDE_CREDENTIALS  — JSON blob materialized to
#      $HOME/.claude/.credentials.json (mode 0600). The Claude.ai
#      subscription path; preferred for non-prod / dev workers.
#   2. ANTHROPIC_API_KEY   — production fallback; bootstraps
#      $HOME/.claude.json with a customApiKeyResponses approval block.
#
# Modeled on ~/Desktop/Code/rclaude/entrypoint.sh (the prior art that
# proved this approach against `claude --dangerously-skip-permissions`
# in a headless ubuntu container) and
# ~/Desktop/Code/agent-experiments/recurring/docker/agent-worker (which
# established the CLAUDE_CREDENTIALS env-var pattern).
#
# IMPORTANT: never `set -x` in this script — it would echo
# CLAUDE_CREDENTIALS to logs. Never echo $CLAUDE_CREDENTIALS or the API
# key directly. The unset calls below scrub them from /proc/<pid>/environ
# before exec.
set -euo pipefail

mkdir -p "$HOME/.claude"

# ---------------------------------------------------------------- OAuth
# Precedence (deliberately on-disk-first so PVC-persisted refreshes are
# not clobbered by a stale Secret on every pod restart — see tyf.3):
#   1. existing $HOME/.claude/.credentials.json (PVC mount, bind-mount)
#   2. CLAUDE_CREDENTIALS env (Secret seed on first boot)
#   3. ANTHROPIC_API_KEY env (production fallback)
PO_AUTH_MODE="apikey"
PO_AUTH_SOURCE=""
if [[ -s "$HOME/.claude/.credentials.json" ]]; then
  # On-disk wins. Either docker-compose bind-mount, k8s PVC at
  # $HOME/.claude/, or a previously-materialized file that the Claude
  # CLI has since refreshed in place. We must NOT overwrite from
  # CLAUDE_CREDENTIALS here or option (a) PVC persistence breaks.
  unset CLAUDE_CREDENTIALS || true
  unset ANTHROPIC_API_KEY || true
  PO_AUTH_MODE="oauth"
  PO_AUTH_SOURCE="disk"
elif [[ -n "${CLAUDE_CREDENTIALS:-}" ]]; then
  umask 077
  # printf '%s' (not echo) so JSON braces / backslashes aren't reinterpreted.
  printf '%s' "$CLAUDE_CREDENTIALS" > "$HOME/.claude/.credentials.json"
  chmod 600 "$HOME/.claude/.credentials.json"
  unset CLAUDE_CREDENTIALS
  unset ANTHROPIC_API_KEY
  PO_AUTH_MODE="oauth"
  PO_AUTH_SOURCE="env"
fi
# One-line audit log: which path won. Never echoes secret contents.
echo "po-entrypoint: auth=${PO_AUTH_MODE} source=${PO_AUTH_SOURCE:-apikey}" >&2

# ----------------------------------------------------------- API-key path
# Workers normally need ANTHROPIC_API_KEY to actually call Claude when
# OAuth isn't in use. The stub backend (PO_BACKEND=stub) doesn't, so we
# only enforce the key when a real backend is selected and we're not
# already authenticated via OAuth.
if [[ "$PO_AUTH_MODE" == "apikey" ]]; then
  case "${PO_BACKEND:-cli}" in
    stub)
      : "${ANTHROPIC_API_KEY:=stub-not-required}"
      ;;
    *)
      if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
        echo "error: no Claude auth configured (PO_BACKEND=${PO_BACKEND:-cli})." >&2
        echo "       set CLAUDE_CREDENTIALS (OAuth, preferred for dev) or" >&2
        echo "       ANTHROPIC_API_KEY (API key, production fallback)." >&2
        echo "       set PO_BACKEND=stub to run without a real Claude key." >&2
        exit 64
      fi
      ;;
  esac
fi

# Bootstrap Claude Code config so it skips onboarding and trusts
# /workspace + /rig. Idempotent: we always overwrite (the file is
# per-container). In OAuth mode we drop the customApiKeyResponses
# block — Claude Code reads creds from the credentials file instead.
if [[ "$PO_AUTH_MODE" == "oauth" ]]; then
  cat > "$HOME/.claude.json" <<CLEOF
{
  "numStartups": 1,
  "hasCompletedOnboarding": true,
  "bypassPermissionsModeAccepted": true,
  "projects": {
    "/home/coder": { "hasTrustDialogAccepted": true, "allowedTools": [] },
    "/workspace":  { "hasTrustDialogAccepted": true, "allowedTools": [] },
    "/rig":        { "hasTrustDialogAccepted": true, "allowedTools": [] }
  }
}
CLEOF
else
  ANTHROPIC_KEY_SUFFIX="${ANTHROPIC_API_KEY: -20}"
  cat > "$HOME/.claude.json" <<CLEOF
{
  "numStartups": 1,
  "hasCompletedOnboarding": true,
  "hasAcknowledgedCustomApiKey": true,
  "bypassPermissionsModeAccepted": true,
  "customApiKeyResponses": {
    "approved": ["${ANTHROPIC_KEY_SUFFIX}"],
    "rejected": []
  },
  "projects": {
    "/home/coder": { "hasTrustDialogAccepted": true, "allowedTools": [] },
    "/workspace":  { "hasTrustDialogAccepted": true, "allowedTools": [] },
    "/rig":        { "hasTrustDialogAccepted": true, "allowedTools": [] }
  }
}
CLEOF
fi

# settings.json: only write the default if no settings file is already
# present. The image-baked `~/.claude/settings.json` (from the
# `claude-context` build stage — see Dockerfile and
# scripts/sync-claude-context.sh, prefect-orchestration-tyf.2) and any
# ConfigMap-mounted override must win over this fallback.
if [[ ! -f "$HOME/.claude/settings.json" ]]; then
  cat > "$HOME/.claude/settings.json" <<'CSEOF'
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "theme": "dark"
}
CSEOF
fi

# ConfigMap override overlay (prefect-orchestration-tyf.2).
# When deployed with the optional `claude-context-overrides` ConfigMap
# projected at /home/coder/.claude-overrides/, copy its files on top of
# the baked tree so operators can update CLAUDE.md / settings.json /
# commands without rebuilding the image. Pod restart still required for
# pickup — this is a per-boot overlay, not a live mount.
OVERRIDES_DIR="${PO_CLAUDE_OVERRIDES_DIR:-$HOME/.claude-overrides}"
if [[ -d "$OVERRIDES_DIR" ]]; then
  # cp -rT keeps the existing baked files for anything the overrides
  # don't replace (skills/, prompts/). Trailing slash on src copies
  # contents, not the dir itself.
  if cp -rT "$OVERRIDES_DIR/" "$HOME/.claude/" 2>/dev/null; then
    echo "po-entrypoint: applied claude-context overrides from $OVERRIDES_DIR" >&2
  else
    echo "po-entrypoint: warning — failed to apply overrides from $OVERRIDES_DIR" >&2
  fi
fi

# Ensure ~/.local/bin (uv-tool installs) is on PATH for whatever runs next.
export PATH="$HOME/.local/bin:$PATH"

# Default the rig path env so flows inside the container resolve to the
# bind/PVC mount unless the caller overrides it.
export PO_RIG_PATH="${PO_RIG_PATH:-/rig}"

export PO_AUTH_MODE
export PO_AUTH_SOURCE

exec "$@"
