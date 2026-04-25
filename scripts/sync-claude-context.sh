#!/usr/bin/env bash
# scripts/sync-claude-context.sh — populate ./claude-context/ from $HOME/.claude
# so the worker image build (`docker build --build-context
# claude-context=./claude-context …`) can bake the user's CLAUDE.md, prompts,
# skills, and commands into /home/coder/.claude/ inside the pod.
#
# Whitelist-only. Refuses to copy credentials, history, caches, or
# session-state. Sanitizes settings.json (drops hooks, mcpServers, anything
# with token/key/secret in the key name).
#
# Usage:
#   scripts/sync-claude-context.sh                       # default SRC/DEST
#   SRC=$HOME/.claude DEST=./claude-context \
#     scripts/sync-claude-context.sh --force
#   scripts/sync-claude-context.sh --emit-configmap k8s/claude-context-overrides.yaml
#
# Flags:
#   --force                   skip the "DEST not empty" prompt
#   --emit-configmap PATH     also emit a ConfigMap manifest at PATH for the
#                             small overrideable subset (CLAUDE.md +
#                             commands/ + settings.json). Skills/prompts are
#                             too big for a ConfigMap and stay baked.
#
# Issue: prefect-orchestration-tyf.2

set -euo pipefail

SRC="${SRC:-$HOME/.claude}"
DEST="${DEST:-./claude-context}"
FORCE=0
EMIT_CONFIGMAP=""
CONFIGMAP_NAME="${CONFIGMAP_NAME:-claude-context-overrides}"
CONFIGMAP_NAMESPACE="${CONFIGMAP_NAMESPACE:-default}"
# ConfigMap is capped near 1 MiB total. Leave headroom; warn above this.
CONFIGMAP_BUDGET_BYTES="${CONFIGMAP_BUDGET_BYTES:-921600}"  # ~900 KiB

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --emit-configmap) EMIT_CONFIGMAP="${2:?--emit-configmap needs a path}"; shift 2 ;;
    -h|--help)
      sed -n '1,30p' "$0"
      exit 0
      ;;
    *)
      echo "error: unknown arg: $1" >&2
      exit 64
      ;;
  esac
done

require() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: $1 not found on PATH (needed: $2)" >&2
    exit 69
  }
}

require rsync "whitelist tree copy"
require jq    "settings.json sanitization"

if [[ ! -d "$SRC" ]]; then
  echo "error: SRC=$SRC is not a directory" >&2
  exit 66
fi

# DEST handling: refuse to wipe a non-empty existing dir without --force.
if [[ -e "$DEST" ]]; then
  if [[ ! -d "$DEST" ]]; then
    echo "error: DEST=$DEST exists and is not a directory" >&2
    exit 73
  fi
  # Anything other than .gitkeep is "real" content.
  has_content="$(find "$DEST" -mindepth 1 -not -name .gitkeep -print -quit 2>/dev/null || true)"
  if [[ -n "$has_content" && $FORCE -ne 1 ]]; then
    echo "DEST=$DEST is not empty. Re-run with --force to overwrite." >&2
    exit 75
  fi
fi

mkdir -p "$DEST"

# ----------------------------------------------------------------- whitelist
# Per the plan: CLAUDE.md, prompts/, skills/, commands/, sanitized settings.json.
# Anything else from $SRC is dropped.

# 1. CLAUDE.md (file)
if [[ -f "$SRC/CLAUDE.md" ]]; then
  cp -f "$SRC/CLAUDE.md" "$DEST/CLAUDE.md"
fi

# 2. prompts/ skills/ commands/ (directories) — rsync with --delete so a
# previous DEST is converged to the current SRC, but only within those dirs.
for dir in prompts skills commands; do
  if [[ -d "$SRC/$dir" ]]; then
    mkdir -p "$DEST/$dir"
    rsync -a --delete \
      --exclude '.DS_Store' \
      --exclude '*.pyc' \
      --exclude '__pycache__/' \
      "$SRC/$dir/" "$DEST/$dir/"
  else
    rm -rf "$DEST/$dir"
  fi
done

# 3. settings.json — sanitize with jq, allowlist top-level keys only.
# Whitelist of keys safe to ship into a pod. Anything else (hooks,
# mcpServers, *token*, *key*, *secret*, project-specific paths) is dropped.
sanitize_settings() {
  local input="$1"
  local output="$2"
  # If no input file, write a minimal default.
  if [[ ! -f "$input" ]]; then
    printf '%s\n' '{"$schema":"https://json.schemastore.org/claude-code-settings.json","theme":"dark"}' \
      | jq '.' > "$output"
    return
  fi
  # Allowlist top-level keys; explicitly strip anything containing token/key/secret.
  jq '
    def safe_keys: ["$schema", "theme", "model", "permissions", "outputStyle", "statusLine"];
    . as $root
    | reduce safe_keys[] as $k ({}; if ($root | has($k)) then .[$k] = $root[$k] else . end)
    | with_entries(
        select(
          (.key | ascii_downcase | test("token|secret|apikey|api_key|credential")) | not
        )
      )
  ' "$input" > "$output.tmp"
  mv "$output.tmp" "$output"
}

sanitize_settings "$SRC/settings.json" "$DEST/settings.json"

# ------------------------------------------------------------ refusal scan
# Belt-and-braces: even though we whitelist, hard-fail if any of these
# slip through (e.g. a future maintainer expands the whitelist).
REFUSE_PATTERNS=(
  '.credentials.json'
  'projects'
  'history.jsonl'
  'cache'
  'image-cache'
  'paste-cache'
  'file-history'
  'secrets'
  'session-env'
  'sessions'
  'ide'
  'backups'
  'archive'
  'plans'
  'plugins'
  'memory'
  'agents'
  'hooks'
  'CLAUDE.md.bkp'
)
violations=()
for p in "${REFUSE_PATTERNS[@]}"; do
  while IFS= read -r -d '' hit; do
    violations+=("$hit")
  done < <(find "$DEST" -name "$p" -print0 2>/dev/null || true)
done
if (( ${#violations[@]} > 0 )); then
  echo "error: refusal-list patterns found under $DEST:" >&2
  printf '  %s\n' "${violations[@]}" >&2
  echo "       remove and re-run." >&2
  exit 77
fi

# Settings JSON token grep — fail loudly if a token-like string survived.
if grep -RInE '(sk-[a-zA-Z0-9_-]{16,}|xox[abp]-[a-zA-Z0-9-]{10,}|ghp_[a-zA-Z0-9]{20,}|"token"\s*:|"apiKey"\s*:|"secret"\s*:)' "$DEST/settings.json" >/dev/null 2>&1; then
  echo "error: $DEST/settings.json appears to contain a secret-like field" >&2
  echo "       sanitization missed something — investigate before shipping" >&2
  exit 78
fi

# ----------------------------------------------------------------- sizes
human_size() { du -sh "$1" 2>/dev/null | awk '{print $1}'; }
bytes_size() { du -sb "$1" 2>/dev/null | awk '{print $1}'; }

echo "synced ~/.claude → $DEST:"
for entry in CLAUDE.md prompts skills commands settings.json; do
  if [[ -e "$DEST/$entry" ]]; then
    printf '  %-14s %s\n' "$entry" "$(human_size "$DEST/$entry")"
  else
    printf '  %-14s (absent in source)\n' "$entry"
  fi
done

# Small-subset (configmap-able) total: CLAUDE.md + commands/ + settings.json.
small_bytes=0
for entry in CLAUDE.md commands settings.json; do
  if [[ -e "$DEST/$entry" ]]; then
    n=$(bytes_size "$DEST/$entry"); small_bytes=$(( small_bytes + n ))
  fi
done
echo "  small-subset total: $small_bytes bytes (budget: $CONFIGMAP_BUDGET_BYTES)"
if (( small_bytes > CONFIGMAP_BUDGET_BYTES )); then
  echo "  warning: small-subset exceeds ConfigMap budget; --emit-configmap may be rejected by k8s." >&2
fi

# ------------------------------------------------------- emit configmap
if [[ -n "$EMIT_CONFIGMAP" ]]; then
  require kubectl "--emit-configmap"
  args=( create configmap "$CONFIGMAP_NAME"
         --namespace "$CONFIGMAP_NAMESPACE"
         --dry-run=client -o yaml )
  [[ -f "$DEST/CLAUDE.md" ]]     && args+=( --from-file=CLAUDE.md="$DEST/CLAUDE.md" )
  [[ -f "$DEST/settings.json" ]] && args+=( --from-file=settings.json="$DEST/settings.json" )
  if [[ -d "$DEST/commands" ]]; then
    # Flatten commands/ (one entry per *.md file). ConfigMap keys can't
    # contain slashes; subdirs aren't supported. We document this.
    while IFS= read -r -d '' f; do
      key="$(basename "$f")"
      args+=( --from-file="commands.${key}=${f}" )
    done < <(find "$DEST/commands" -maxdepth 2 -type f -name '*.md' -print0)
  fi
  mkdir -p "$(dirname "$EMIT_CONFIGMAP")"
  kubectl "${args[@]}" > "$EMIT_CONFIGMAP"
  echo "emitted ConfigMap manifest → $EMIT_CONFIGMAP"
fi
