#!/usr/bin/env bash
# scripts/snakes-demo/seed-beads.sh
#
# Seed 100 dot-suffix children under the snakes-demo epic, one per language.
# Slots <epic-id>.7 .. <epic-id>.106 (slots .1-.6 are reserved for prereqs).
#
# Reads the language list from <rig>/engdocs/languages.txt when present,
# otherwise falls back to the hardcoded SNAKE_LANGUAGES list below.
#
# Idempotent: existing beads at a target slot are skipped. With --force the
# existing bead is closed and a fresh one is created in its place.
#
# See engdocs/snakes-demo.md for the full demo walkthrough.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: seed-beads.sh --epic-id ID [options]

Required:
  --epic-id ID         Epic bead id; children created at <id>.7 ... <id>.106

Optional:
  --rig-path PATH      Run bd inside this directory (default: cwd)
  --languages FILE     Path to a languages list (one per line, blank/# ignored).
                       Default: <rig-path>/engdocs/languages.txt, or hardcoded
                       fallback if that file does not exist.
  --priority N         Priority for child beads (0-4, default 3)
  --force              Close-and-recreate existing beads at target slots
  --dry-run            Print what would be created; do not call bd create
  -h, --help           Show this help

Notes:
  - Slot offset is fixed at +6 (slot .7 = language #1 = Python).
  - Children have no bd dep edges; they are all parallel-ready.
EOF
}

EPIC_ID=""
RIG_PATH=""
LANGUAGES_FILE=""
PRIORITY=3
FORCE=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --epic-id)      EPIC_ID="${2:-}"; shift 2 ;;
    --epic-id=*)    EPIC_ID="${1#*=}"; shift ;;
    --rig-path)     RIG_PATH="${2:-}"; shift 2 ;;
    --rig-path=*)   RIG_PATH="${1#*=}"; shift ;;
    --languages)    LANGUAGES_FILE="${2:-}"; shift 2 ;;
    --languages=*)  LANGUAGES_FILE="${1#*=}"; shift ;;
    --priority)     PRIORITY="${2:-}"; shift 2 ;;
    --priority=*)   PRIORITY="${1#*=}"; shift ;;
    --force)        FORCE=1; shift ;;
    --dry-run)      DRY_RUN=1; shift ;;
    -h|--help)      usage; exit 0 ;;
    *)
      printf 'unknown flag: %s\n\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$EPIC_ID" ]]; then
  printf 'error: --epic-id is required\n\n' >&2
  usage >&2
  exit 2
fi

if [[ -n "$RIG_PATH" ]]; then
  if [[ ! -d "$RIG_PATH" ]]; then
    printf 'error: --rig-path %q does not exist\n' "$RIG_PATH" >&2
    exit 2
  fi
  cd "$RIG_PATH"
fi

if ! command -v bd >/dev/null 2>&1; then
  printf 'error: bd not on PATH\n' >&2
  exit 2
fi

# Hardcoded fallback list — order matches slot .7 = Python through .106 = Logo.
# Mirrors the language list in the prefect-orchestration-5wk epic description.
SNAKE_LANGUAGES=(
  Python Rust Go TypeScript C C++ Zig Lua Ruby Java
  Kotlin Swift Haskell OCaml Clojure Scheme "Common Lisp" Elixir Erlang Crystal
  Nim V Odin Janet Racket Forth Tcl Perl PHP Bash
  Awk Fish Zsh PowerShell Pascal Ada Fortran COBOL D Vala
  Hare Roc Gleam Elm Idris Agda PureScript ReScript ReasonML F#
  Scala Groovy Dart Julia R MATLAB Octave Mathematica J K
  APL BQN Uiua Rebol Red Smalltalk Self Io Factor Pony
  Chapel Cilk SystemVerilog VHDL SPARK Eiffel ABAP Algol-68 Modula-2 BCPL
  BASIC QBASIC ColdFusion LiveCode AutoHotkey Brainfuck Whitespace Befunge Malbolge Piet
  LOLCODE Shakespeare INTERCAL Chef ArnoldC Rockstar PostScript Verilog Solidity Logo
)

# Resolve the languages list source: explicit flag > rig file > hardcoded.
declare -a languages=()
declare languages_source
if [[ -n "$LANGUAGES_FILE" ]]; then
  if [[ ! -f "$LANGUAGES_FILE" ]]; then
    printf 'error: --languages %q does not exist\n' "$LANGUAGES_FILE" >&2
    exit 2
  fi
  languages_source="$LANGUAGES_FILE"
elif [[ -f engdocs/languages.txt ]]; then
  languages_source="engdocs/languages.txt"
else
  languages_source="<hardcoded fallback>"
fi

if [[ "$languages_source" == "<hardcoded fallback>" ]]; then
  languages=("${SNAKE_LANGUAGES[@]}")
else
  while IFS= read -r line || [[ -n "$line" ]]; do
    # Strip leading "N " or "N. " slot prefixes if present.
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -z "$line" || "$line" == "#"* ]] && continue
    line="${line#[0-9]* }"
    line="${line#[0-9]*. }"
    languages+=("$line")
  done < "$languages_source"
fi

if (( ${#languages[@]} != 100 )); then
  printf 'error: expected 100 languages, got %d (source: %s)\n' \
    "${#languages[@]}" "$languages_source" >&2
  exit 2
fi

printf 'languages source: %s\n' "$languages_source" >&2
printf 'epic-id: %s   slots: %s.7 .. %s.106   force=%d dry-run=%d\n' \
  "$EPIC_ID" "$EPIC_ID" "$EPIC_ID" "$FORCE" "$DRY_RUN" >&2

DESCRIPTION_TEMPLATE='Create snakes/<language>/ with a runnable Snake implementation, a one-line README.md showing how to run it, any build files required. Single file preferred when idiomatic. Commit on branch demo/snakes-<language> in the rig. Do NOT push. Use the minimal-task formula.'

slot_offset=6
created=0
skipped=0
recreated=0

for i in "${!languages[@]}"; do
  lang="${languages[$i]}"
  slot=$(( i + 1 + slot_offset ))
  bead_id="${EPIC_ID}.${slot}"
  title="Implement Snake in ${lang}"
  description="${DESCRIPTION_TEMPLATE//<language>/${lang}}"

  if bd show "$bead_id" --json >/dev/null 2>&1; then
    if (( FORCE == 1 )); then
      if (( DRY_RUN == 1 )); then
        printf '[dry-run] would close-and-recreate %s (%s)\n' "$bead_id" "$lang"
      else
        bd close "$bead_id" --reason "seed-beads.sh --force: recreating" >/dev/null
        bd create --type=task --priority="$PRIORITY" \
          --id "$bead_id" \
          --title="$title" \
          --description="$description" >/dev/null
      fi
      recreated=$(( recreated + 1 ))
      continue
    fi
    skipped=$(( skipped + 1 ))
    continue
  fi

  if (( DRY_RUN == 1 )); then
    printf '[dry-run] would create %s — %s\n' "$bead_id" "$title"
  else
    bd create --type=task --priority="$PRIORITY" \
      --id "$bead_id" \
      --title="$title" \
      --description="$description" >/dev/null
  fi
  created=$(( created + 1 ))
done

printf 'done: %d created, %d skipped, %d recreated\n' \
  "$created" "$skipped" "$recreated" >&2
