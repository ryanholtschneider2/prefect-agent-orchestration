#!/usr/bin/env bash
# provision-rig.sh — provision a fresh snakes-demo rig.
#
# Idempotent: if RIG_PATH already contains a `.beads/` marker, refuse to
# clobber unless --force is passed. If the path exists but is NOT a beads
# rig, refuse unconditionally (don't risk wiping unrelated user data).
#
# Usage:
#   scripts/snakes-demo/provision-rig.sh [--force] [--remote <url>]
#
# Environment:
#   RIG_PATH          target directory (default: ~/Desktop/Code/personal/snakes-demo)
#   GIT_AUTHOR_NAME   git user.name for the rig (fallback: global config)
#   GIT_AUTHOR_EMAIL  git user.email for the rig (fallback: global config)
#
# Acceptance criteria (prefect-orchestration-5wk.4):
#   - script idempotent
#   - resulting rig has .git, .beads, README.md, CLAUDE.md, engdocs/languages.txt, no snakes/
#   - shellcheck strict mode passes

set -euo pipefail

# Locate self so the sibling languages.txt resolves regardless of cwd.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
LANGUAGES_SRC="${SCRIPT_DIR}/languages.txt"

force=0
remote_url=""

usage() {
    cat <<'EOF'
Usage: provision-rig.sh [--force] [--remote <url>]

  --force           if RIG_PATH already contains a .beads/ marker, wipe and recreate
  --remote <url>    set up `origin` remote pointing at <url> (no push)
  -h, --help        show this help

Environment:
  RIG_PATH          target directory (default: ~/Desktop/Code/personal/snakes-demo)
  GIT_AUTHOR_NAME   git user.name (fallback: global config)
  GIT_AUTHOR_EMAIL  git user.email (fallback: global config)
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) force=1; shift ;;
        --remote)
            [[ $# -ge 2 ]] || { echo "error: --remote requires a URL" >&2; exit 2; }
            remote_url="$2"; shift 2 ;;
        --remote=*) remote_url="${1#--remote=}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "error: unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

RIG_PATH="${RIG_PATH:-$HOME/Desktop/Code/personal/snakes-demo}"

# Resolve absolute path without requiring it to exist yet.
RIG_PATH_ABS="$(cd -- "$(dirname -- "$RIG_PATH")" 2>/dev/null && pwd -P)/$(basename -- "$RIG_PATH")" || RIG_PATH_ABS="$RIG_PATH"

# Safety guards on destructive paths.
case "$RIG_PATH_ABS" in
    "" | "/" | "$HOME" | "$HOME/")
        echo "error: refuse to operate on dangerous path: $RIG_PATH_ABS" >&2; exit 1 ;;
esac

[[ -f "$LANGUAGES_SRC" ]] || { echo "error: missing canonical languages list at $LANGUAGES_SRC" >&2; exit 1; }

# Idempotency gate.
if [[ -e "$RIG_PATH_ABS" ]]; then
    if [[ -d "$RIG_PATH_ABS/.beads" ]]; then
        if [[ "$force" -eq 1 ]]; then
            echo "[provision-rig] --force: wiping existing rig at $RIG_PATH_ABS"
            rm -rf -- "$RIG_PATH_ABS"
        else
            echo "error: rig already exists at $RIG_PATH_ABS — use --force to wipe and recreate" >&2
            exit 1
        fi
    else
        echo "error: $RIG_PATH_ABS exists but is not a snakes-demo rig (no .beads/); refusing to touch it" >&2
        exit 1
    fi
fi

# Resolve git author from env or global config; fail loud if neither.
author_name="${GIT_AUTHOR_NAME:-$(git config --global --get user.name 2>/dev/null || true)}"
author_email="${GIT_AUTHOR_EMAIL:-$(git config --global --get user.email 2>/dev/null || true)}"
if [[ -z "$author_name" || -z "$author_email" ]]; then
    echo "error: git author not set — export GIT_AUTHOR_NAME and GIT_AUTHOR_EMAIL or set global git config" >&2
    exit 1
fi

mkdir -p -- "$RIG_PATH_ABS"
cd -- "$RIG_PATH_ABS"

# `git init -b main` is git >=2.28; fall back via symbolic-ref for older.
if ! git init -b main >/dev/null 2>&1; then
    git init >/dev/null
    git symbolic-ref HEAD refs/heads/main
fi

git config user.name  "$author_name"
git config user.email "$author_email"

mkdir -p engdocs

# engdocs/languages.txt: strip leading `#` comment lines from canonical source.
grep -v '^#' -- "$LANGUAGES_SRC" > engdocs/languages.txt

cat > README.md <<'EOF'
# snakes-demo

100-language Snake implementation showcase. One bead per language, fanned
out in parallel by the [`po`](../prefect-orchestration) actor-critic flow.

Each child bead asks an agent to implement Snake in a single language and
land it under `snakes/<language>/` on a branch named `demo/snakes-<language>`.

See `engdocs/languages.txt` for the canonical slot-N -> language mapping.
EOF

cat > CLAUDE.md <<'EOF'
# CLAUDE.md — snakes-demo rig

You are implementing the game Snake. Each child bead asks for a single
language. Create `snakes/<language>/` with the implementation, a one-line
README on how to run it, and any build files. Branch
`demo/snakes-<language>`. Do NOT push.

## Rules

- One language per bead. Don't touch siblings.
- Single-file implementation when idiomatic; multi-file fine for langs
  that need a build system (Rust, Go modules, etc.).
- A short `README.md` inside `snakes/<language>/` showing how to run.
- Branch name: `demo/snakes-<language>` (lowercase, hyphenate spaces).
- Commit on the branch; do **not** push.
- Lint with whatever the language's standard tool is. Skip if no
  free/easy linter exists.
- If the language is exotic enough that a working impl is impractical
  (Malbolge, Piet, Whitespace), commit a best-effort attempt + notes
  in the README explaining what does/doesn't work.

## Languages

See `engdocs/languages.txt` — slot N maps to a language; the seeder bead
(prefect-orchestration-5wk.5) materialises one child bead per slot.

## bd backend

This rig was provisioned with `bd init` (embedded dolt). For a single
operator running children sequentially this is fine. **If you plan to run
the 100 children fanned-out in parallel through PO**, switch to
dolt-server first to avoid the embedded-dolt exclusive-lock contention:

    bd init --server --server-host=127.0.0.1 --server-port=3307 \
            --server-user=root --database=snakes-demo
    # in another shell, from a directory with the dolt DB:
    dolt sql-server -P 3307 --user root

See the parent project's `CLAUDE.md` for full guidance.
EOF

bd init >/dev/null

if [[ -n "$remote_url" ]]; then
    git remote add origin "$remote_url"
fi

# Initial commit. `git add -A` is acceptable here because this rig is
# greenfield with no concurrent workers — the parent repo CLAUDE.md's
# warning about `-A` applies to flows running across active rigs.
git add -A
git -c commit.gpgsign=false commit -m "Initial snakes-demo rig" >/dev/null

echo "[provision-rig] OK: $RIG_PATH_ABS"
