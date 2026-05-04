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
PO_AUTH_POOL_INDEX=""
PO_AUTH_POOL_SIZE=""
PO_SELECTED_CLAUDE_OAUTH_TOKEN_INDEX=""
PO_SELECTED_CLAUDE_OAUTH_TOKEN_COUNT=""

# ----------------------------------------------- Multi-account pool resolution
# CLAUDE_CREDENTIALS_POOL / ANTHROPIC_API_KEY_POOL — JSON arrays. When the
# corresponding single-account env is unset, deterministically pick one slot
# based on HOSTNAME so each replica routes to the same account across restarts.
# Override with PO_CREDENTIALS_POOL_INDEX / PO_API_KEY_POOL_INDEX (test hook).
# Single-account envs always win — the pool only fills in when they're absent.
# Hash a string deterministically into [0, n). Uses sha256 (portable across
# alpine/ubuntu) and the first 8 hex chars (32-bit). For StatefulSet-style
# ordinal hostnames (`worker-7`), short-circuit to `7 % n` so spread is exact
# when replicas % pool_size == 0. Generic Deployment pods get the sha256-mod
# fallback (statistically even, not exact).
_po_pick_index() {
  local hostname="$1" size="$2" idx
  if [[ "$hostname" =~ -([0-9]+)$ ]]; then
    idx="${BASH_REMATCH[1]}"
  else
    local hex
    hex=$(printf '%s' "$hostname" | sha256sum | cut -c1-8)
    idx=$((16#$hex))
  fi
  echo $(( idx % size ))
}

if [[ -n "${PO_CLAUDE_OAUTH_TOKEN_FILE:-}" && -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
  if [[ ! -f "${PO_CLAUDE_OAUTH_TOKEN_FILE}" ]]; then
    echo "error: PO_CLAUDE_OAUTH_TOKEN_FILE does not exist: ${PO_CLAUDE_OAUTH_TOKEN_FILE}" >&2
    exit 64
  fi
  if mapfile -t po_oauth_tokens < <(grep -v '^[[:space:]]*#' "${PO_CLAUDE_OAUTH_TOKEN_FILE}" | sed '/^[[:space:]]*$/d'); then
    token_count="${#po_oauth_tokens[@]}"
  else
    token_count=0
  fi
  if (( token_count < 1 )); then
    echo "error: PO_CLAUDE_OAUTH_TOKEN_FILE must contain at least one non-empty token line" >&2
    exit 64
  fi
  if [[ -n "${PO_CLAUDE_OAUTH_TOKEN_INDEX:-}" ]]; then
    token_idx="${PO_CLAUDE_OAUTH_TOKEN_INDEX}"
  else
    token_idx=$(_po_pick_index "${HOSTNAME:-localhost}" "$token_count")
  fi
  if (( token_idx < 0 || token_idx >= token_count )); then
    echo "error: PO_CLAUDE_OAUTH_TOKEN_INDEX=$token_idx out of range [0,$token_count)" >&2
    exit 64
  fi
  export CLAUDE_CODE_OAUTH_TOKEN="${po_oauth_tokens[$token_idx]}"
  PO_SELECTED_CLAUDE_OAUTH_TOKEN_INDEX="$token_idx"
  PO_SELECTED_CLAUDE_OAUTH_TOKEN_COUNT="$token_count"
  PO_AUTH_POOL_INDEX="$token_idx"
  PO_AUTH_POOL_SIZE="$token_count"
fi

if [[ -z "${CLAUDE_CREDENTIALS:-}" && -n "${CLAUDE_CREDENTIALS_POOL:-}" ]]; then
  if ! pool_size=$(printf '%s' "$CLAUDE_CREDENTIALS_POOL" | jq 'length' 2>/dev/null) \
       || [[ -z "$pool_size" || "$pool_size" -lt 1 ]]; then
    echo "error: invalid CLAUDE_CREDENTIALS_POOL JSON (must be a non-empty array)" >&2
    exit 64
  fi
  if [[ -n "${PO_CREDENTIALS_POOL_INDEX:-}" ]]; then
    pool_idx="$PO_CREDENTIALS_POOL_INDEX"
  else
    pool_idx=$(_po_pick_index "${HOSTNAME:-localhost}" "$pool_size")
  fi
  if (( pool_idx < 0 || pool_idx >= pool_size )); then
    echo "error: PO_CREDENTIALS_POOL_INDEX=$pool_idx out of range [0,$pool_size)" >&2
    exit 64
  fi
  CLAUDE_CREDENTIALS=$(printf '%s' "$CLAUDE_CREDENTIALS_POOL" | jq -c ".[$pool_idx]")
  export CLAUDE_CREDENTIALS
  PO_AUTH_POOL_INDEX="$pool_idx"
  PO_AUTH_POOL_SIZE="$pool_size"
fi
unset CLAUDE_CREDENTIALS_POOL || true

if [[ -z "${ANTHROPIC_API_KEY:-}" && -n "${ANTHROPIC_API_KEY_POOL:-}" ]]; then
  if ! pool_size=$(printf '%s' "$ANTHROPIC_API_KEY_POOL" | jq 'length' 2>/dev/null) \
       || [[ -z "$pool_size" || "$pool_size" -lt 1 ]]; then
    echo "error: invalid ANTHROPIC_API_KEY_POOL JSON (must be a non-empty array)" >&2
    exit 64
  fi
  if [[ -n "${PO_API_KEY_POOL_INDEX:-}" ]]; then
    pool_idx="$PO_API_KEY_POOL_INDEX"
  else
    pool_idx=$(_po_pick_index "${HOSTNAME:-localhost}" "$pool_size")
  fi
  if (( pool_idx < 0 || pool_idx >= pool_size )); then
    echo "error: PO_API_KEY_POOL_INDEX=$pool_idx out of range [0,$pool_size)" >&2
    exit 64
  fi
  ANTHROPIC_API_KEY=$(printf '%s' "$ANTHROPIC_API_KEY_POOL" | jq -r ".[$pool_idx]")
  export ANTHROPIC_API_KEY
  # Pool index/size only logged for the OAuth path if it was set there;
  # API-key pool overrides only if OAuth pool didn't set it.
  if [[ -z "$PO_AUTH_POOL_INDEX" ]]; then
    PO_AUTH_POOL_INDEX="$pool_idx"
    PO_AUTH_POOL_SIZE="$pool_size"
  fi
fi
unset ANTHROPIC_API_KEY_POOL || true

# ---------------------------------------------------------------- OAuth
# Precedence (deliberately on-disk-first so PVC-persisted refreshes are
# not clobbered by a stale Secret on every pod restart — see tyf.3):
#   1. CLAUDE_CODE_OAUTH_TOKEN env / token file
#   2. existing $HOME/.claude/.credentials.json (PVC mount, bind-mount)
#   3. CLAUDE_CREDENTIALS env (Secret seed on first boot)
#   4. ANTHROPIC_API_KEY env (production fallback)
PO_AUTH_MODE="apikey"
PO_AUTH_SOURCE=""
if [[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
  unset CLAUDE_CREDENTIALS || true
  unset ANTHROPIC_API_KEY || true
  PO_AUTH_MODE="oauth"
  if [[ -n "${PO_CLAUDE_OAUTH_TOKEN_FILE:-}" ]]; then
    PO_AUTH_SOURCE="token-file"
  else
    PO_AUTH_SOURCE="oauth-token"
  fi
elif [[ -s "$HOME/.claude/.credentials.json" ]]; then
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
# When a pool was used, append `pool index=<i> size=<n>` so operators can
# correlate replica → account without dumping credential bodies.
if [[ -n "$PO_AUTH_POOL_INDEX" ]]; then
  echo "po-entrypoint: auth=${PO_AUTH_MODE} source=${PO_AUTH_SOURCE:-apikey} pool index=${PO_AUTH_POOL_INDEX} size=${PO_AUTH_POOL_SIZE}" >&2
else
  echo "po-entrypoint: auth=${PO_AUTH_MODE} source=${PO_AUTH_SOURCE:-apikey}" >&2
fi

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
        echo "       set PO_CLAUDE_OAUTH_TOKEN_FILE / CLAUDE_CODE_OAUTH_TOKEN / CLAUDE_CREDENTIALS or" >&2
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
if [[ -n "$PO_AUTH_POOL_INDEX" ]]; then
  export PO_AUTH_POOL_INDEX
  export PO_AUTH_POOL_SIZE
fi
if [[ -n "$PO_SELECTED_CLAUDE_OAUTH_TOKEN_INDEX" ]]; then
  export PO_CLAUDE_OAUTH_TOKEN_INDEX="$PO_SELECTED_CLAUDE_OAUTH_TOKEN_INDEX"
  export PO_CLAUDE_OAUTH_TOKEN_COUNT="$PO_SELECTED_CLAUDE_OAUTH_TOKEN_COUNT"
fi

exec "$@"
