#!/usr/bin/env bash
# PO worker entrypoint — bootstraps Claude Code so it doesn't hang on
# the trust dialog or onboarding flow inside a container, then `exec`s
# the supplied command.
#
# Modeled on ~/Desktop/Code/rclaude/entrypoint.sh (the prior art that
# proved this approach against `claude --dangerously-skip-permissions`
# in a headless ubuntu container).
set -euo pipefail

# Workers normally need ANTHROPIC_API_KEY to actually call Claude. The
# stub backend (PO_BACKEND=stub) doesn't, so we only enforce the key
# when a real backend is selected.
case "${PO_BACKEND:-cli}" in
  stub)
    : "${ANTHROPIC_API_KEY:=stub-not-required}"
    ;;
  *)
    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
      echo "error: ANTHROPIC_API_KEY is unset (PO_BACKEND=${PO_BACKEND:-cli})." >&2
      echo "       export it before `docker run` / set it on the k8s Secret." >&2
      echo "       set PO_BACKEND=stub to run without a real Claude key." >&2
      exit 64
    fi
    ;;
esac

# Bootstrap Claude Code config so it skips onboarding, accepts the API
# key without a TTY prompt, and trusts /workspace + /rig. Idempotent:
# we always overwrite (the file is per-container).
ANTHROPIC_KEY_SUFFIX="${ANTHROPIC_API_KEY: -20}"
mkdir -p "$HOME/.claude"
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

cat > "$HOME/.claude/settings.json" <<'CSEOF'
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "theme": "dark"
}
CSEOF

# Ensure ~/.local/bin (uv-tool installs) is on PATH for whatever runs next.
export PATH="$HOME/.local/bin:$PATH"

# Default the rig path env so flows inside the container resolve to the
# bind/PVC mount unless the caller overrides it.
export PO_RIG_PATH="${PO_RIG_PATH:-/rig}"

exec "$@"
