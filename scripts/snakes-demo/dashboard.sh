#!/usr/bin/env bash
# scripts/snakes-demo/dashboard.sh
#
# Lay out a 3- or 4-pane recording dashboard for the snakes fanout demo.
# See engdocs/snakes-demo.md for the full walkthrough.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: dashboard.sh --rig-path PATH [options]

Required:
  --rig-path PATH        Absolute path to the rig used by the demo
                         (Pane 3 watches "$RIG_PATH/snakes/")

Optional:
  --namespace NS         Kubernetes namespace for Pane 2 (default: po)
  --epic-id ID           Snakes epic id; enables Pane 4 (po watch <id>)
                         and the Prefect-UI tag filter
  --layout LAYOUT        wezterm | tmux | auto    (default: auto)
  --dry-run              Print the chosen backend + per-pane commands and exit
  -h, --help             Show this help and exit
EOF
}

RIG_PATH=""
NAMESPACE="po"
EPIC_ID=""
LAYOUT="auto"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rig-path)   RIG_PATH="${2:-}"; shift 2 ;;
    --rig-path=*) RIG_PATH="${1#*=}"; shift ;;
    --namespace)  NAMESPACE="${2:-}"; shift 2 ;;
    --namespace=*) NAMESPACE="${1#*=}"; shift ;;
    --epic-id)    EPIC_ID="${2:-}"; shift 2 ;;
    --epic-id=*)  EPIC_ID="${1#*=}"; shift ;;
    --layout)     LAYOUT="${2:-}"; shift 2 ;;
    --layout=*)   LAYOUT="${1#*=}"; shift ;;
    --dry-run)    DRY_RUN=1; shift ;;
    -h|--help)    usage; exit 0 ;;
    *)
      printf 'unknown flag: %s\n\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$RIG_PATH" ]]; then
  printf 'error: --rig-path is required\n\n' >&2
  usage >&2
  exit 2
fi

case "$LAYOUT" in
  wezterm|tmux|auto) ;;
  *)
    printf 'error: --layout must be one of wezterm|tmux|auto (got %q)\n' "$LAYOUT" >&2
    exit 2
    ;;
esac

# Decide backend.
choose_backend() {
  if [[ "$LAYOUT" == "wezterm" ]]; then
    echo wezterm; return
  fi
  if [[ "$LAYOUT" == "tmux" ]]; then
    echo tmux; return
  fi
  # auto: prefer wezterm only when invoked from inside a wezterm pane
  if command -v wezterm >/dev/null 2>&1 && [[ -n "${WEZTERM_PANE:-}" ]]; then
    echo wezterm; return
  fi
  echo tmux
}

BACKEND="$(choose_backend)"

# Compute the URL for Pane 1.
PREFECT_URL="http://localhost:4200/runs"
if [[ -n "$EPIC_ID" ]]; then
  PREFECT_URL="http://localhost:4200/runs?tag=epic:${EPIC_ID}"
fi

# Pane 4 is conditional: only when we have an epic-id AND `po watch` exists.
PANE4_AVAILABLE=0
if [[ -n "$EPIC_ID" ]]; then
  if command -v po >/dev/null 2>&1 && po watch --help >/dev/null 2>&1; then
    PANE4_AVAILABLE=1
  else
    printf 'skipping pane 4: po watch unavailable\n' >&2
  fi
fi

# Per-pane commands. Each must be a single shell string suitable for `bash -c`.
# `exec bash` keeps the pane alive after the foreground command exits, so the
# recording captures the final state instead of an empty terminal.
PANE1_CMD="( command -v xdg-open >/dev/null 2>&1 && xdg-open '${PREFECT_URL}' ) \
|| ( command -v firefox >/dev/null 2>&1 && firefox '${PREFECT_URL}' & ) \
|| printf 'open: %s\n' '${PREFECT_URL}'; exec bash"

PANE2_CMD="kubectl get pods -n '${NAMESPACE}' -w; exec bash"

PANE3_CMD="watch -n 1 \"tree -L 2 '${RIG_PATH}/snakes/' 2>/dev/null || ls -la '${RIG_PATH}/snakes/'\"; exec bash"

PANE4_CMD=""
if [[ "$PANE4_AVAILABLE" -eq 1 ]]; then
  PANE4_CMD="po watch '${EPIC_ID}'; exec bash"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  printf 'backend=%s\n' "$BACKEND"
  printf 'rig_path=%s\n' "$RIG_PATH"
  printf 'namespace=%s\n' "$NAMESPACE"
  printf 'epic_id=%s\n' "${EPIC_ID:-<unset>}"
  case "$BACKEND" in
    wezterm)
      printf 'pane1: wezterm cli split-pane --right -- bash -c %q\n' "$PANE1_CMD"
      printf 'pane2: wezterm cli split-pane --bottom -- bash -c %q\n' "$PANE2_CMD"
      printf 'pane3: wezterm cli split-pane --bottom -- bash -c %q\n' "$PANE3_CMD"
      if [[ -n "$PANE4_CMD" ]]; then
        printf 'pane4: wezterm cli split-pane --bottom -- bash -c %q\n' "$PANE4_CMD"
      fi
      ;;
    tmux)
      printf 'pane1: tmux send-keys (initial pane) %q C-m\n' "$PANE1_CMD"
      printf 'pane2: tmux split-window -h -- bash -c %q\n' "$PANE2_CMD"
      printf 'pane3: tmux split-window -v -- bash -c %q\n' "$PANE3_CMD"
      if [[ -n "$PANE4_CMD" ]]; then
        printf 'pane4: tmux split-window -v -- bash -c %q\n' "$PANE4_CMD"
      fi
      printf 'finalize: tmux select-layout tiled\n'
      ;;
  esac
  exit 0
fi

# Live spawn.
case "$BACKEND" in
  wezterm)
    if ! command -v wezterm >/dev/null 2>&1; then
      printf 'error: --layout wezterm but wezterm not on PATH\n' >&2
      exit 1
    fi
    if [[ -z "${WEZTERM_PANE:-}" ]]; then
      printf 'error: --layout wezterm but $WEZTERM_PANE unset (run from inside a WezTerm pane)\n' >&2
      exit 1
    fi
    wezterm cli split-pane --right --percent 50 -- bash -c "$PANE1_CMD" >/dev/null
    wezterm cli split-pane --bottom --percent 50 -- bash -c "$PANE2_CMD" >/dev/null
    wezterm cli split-pane --bottom --percent 50 -- bash -c "$PANE3_CMD" >/dev/null
    if [[ -n "$PANE4_CMD" ]]; then
      wezterm cli split-pane --bottom --percent 50 -- bash -c "$PANE4_CMD" >/dev/null
    fi
    ;;
  tmux)
    if ! command -v tmux >/dev/null 2>&1; then
      printf 'error: tmux not on PATH (and wezterm pane attach unavailable)\n' >&2
      exit 1
    fi
    if [[ -n "${TMUX:-}" ]]; then
      # Already inside tmux: split the current window in place.
      tmux send-keys "$PANE1_CMD" C-m
      tmux split-window -h "bash -c \"$PANE2_CMD\""
      tmux split-window -v "bash -c \"$PANE3_CMD\""
      if [[ -n "$PANE4_CMD" ]]; then
        tmux split-window -v "bash -c \"$PANE4_CMD\""
      fi
      tmux select-layout tiled
    else
      SESSION="snakes-demo"
      tmux kill-session -t "$SESSION" 2>/dev/null || true
      tmux new-session -d -s "$SESSION" "bash -c \"$PANE1_CMD\""
      tmux split-window -h -t "$SESSION" "bash -c \"$PANE2_CMD\""
      tmux split-window -v -t "$SESSION" "bash -c \"$PANE3_CMD\""
      if [[ -n "$PANE4_CMD" ]]; then
        tmux split-window -v -t "$SESSION" "bash -c \"$PANE4_CMD\""
      fi
      tmux select-layout -t "$SESSION" tiled
      tmux attach -t "$SESSION"
    fi
    ;;
esac
