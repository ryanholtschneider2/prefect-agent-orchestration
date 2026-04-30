#!/usr/bin/env bash
# PO one-line installer.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/<USER>/prefect-orchestration/main/scripts/install.sh | sh
#   curl -fsSL .../install.sh | AGENT=claude sh    # skill only for Claude Code
#   curl -fsSL .../install.sh | AGENT=none   sh    # CLI only, no skill
#   PO_REPO_URL=https://github.com/me/fork.git AGENT=claude sh install.sh
#
# What it does:
#   1. Verify (or install) prerequisites: uv, dolt, bd
#   2. Clone (or update) the prefect-orchestration repo to ~/.local/share/prefect-orchestration
#   3. `make install AGENT=<all|claude|cursor|aider|none>` from that checkout
#
# Honors:
#   PO_REPO_URL   git URL to clone from (default: https://github.com/anthropics/prefect-orchestration.git)
#   PO_REPO_REF   git ref to checkout (default: main)
#   PO_INSTALL_DIR  where to clone to (default: ~/.local/share/prefect-orchestration)
#   AGENT         all | claude | cursor | aider | none (default: all)

set -euo pipefail

PO_REPO_URL="${PO_REPO_URL:-https://github.com/anthropics/prefect-orchestration.git}"
PO_REPO_REF="${PO_REPO_REF:-main}"
PO_INSTALL_DIR="${PO_INSTALL_DIR:-$HOME/.local/share/prefect-orchestration}"
AGENT="${AGENT:-all}"

log() { printf '\033[1;34m→\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# 1. Prerequisites.

if ! command -v uv >/dev/null 2>&1; then
    log "installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Source the env so `uv` is on PATH for the rest of this script.
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    command -v uv >/dev/null 2>&1 || die "uv install failed; rerun after sourcing your shell rc"
    ok "uv installed"
else
    ok "uv found"
fi

if ! command -v dolt >/dev/null 2>&1; then
    log "installing dolt (sql-server backend for the bd issue tracker)"
    curl -L https://github.com/dolthub/dolt/releases/latest/download/install.sh | bash
    command -v dolt >/dev/null 2>&1 || warn "dolt install may need a new shell; continuing"
else
    ok "dolt found"
fi

if ! command -v bd >/dev/null 2>&1; then
    warn "bd (beads) not on PATH"
    warn "install from https://github.com/steveyegge/beads (PO uses bd as its task tracker)"
    warn "continuing — \`po\` itself works without bd, but real flows need it"
else
    ok "bd found"
fi

# 2. Clone or update the repo.

if [ -d "$PO_INSTALL_DIR/.git" ]; then
    log "updating $PO_INSTALL_DIR (git pull)"
    git -C "$PO_INSTALL_DIR" fetch --quiet origin "$PO_REPO_REF" 2>&1 || warn "fetch failed; continuing with local copy"
    git -C "$PO_INSTALL_DIR" checkout --quiet "$PO_REPO_REF" 2>&1 || warn "checkout failed; continuing"
    git -C "$PO_INSTALL_DIR" pull --quiet --ff-only origin "$PO_REPO_REF" 2>&1 || warn "pull failed; continuing"
elif [ -d "$PO_INSTALL_DIR" ]; then
    die "$PO_INSTALL_DIR exists but is not a git checkout. Move it aside or set PO_INSTALL_DIR=elsewhere."
else
    log "cloning $PO_REPO_URL → $PO_INSTALL_DIR"
    mkdir -p "$(dirname "$PO_INSTALL_DIR")"
    git clone --quiet --branch "$PO_REPO_REF" "$PO_REPO_URL" "$PO_INSTALL_DIR"
fi
ok "repo at $PO_INSTALL_DIR"

# 3. Hand off to the Makefile.

log "make install AGENT=$AGENT"
make -C "$PO_INSTALL_DIR" install AGENT="$AGENT"

# 4. Hint at next steps.

echo
ok "PO installed. Verify with:"
echo "    po list             # available formulas"
echo "    po doctor           # wiring health check"
echo
echo "Add a formula pack:"
echo "    po packs install --editable /path/to/pack"
