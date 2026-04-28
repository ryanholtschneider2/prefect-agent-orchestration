# Triage — prefect-orchestration-5wk.6

## Summary
Ship a 3-panel (optionally 4) recording dashboard script for the "snakes" fanout demo: `scripts/snakes-demo/dashboard.sh` accepting `--rig-path` and `--namespace`. Detect WezTerm via `wezterm cli spawn`; otherwise fall back to a tmux split-window layout. Panes show the Prefect UI flow-runs view, `kubectl get pods -n <ns> -w`, and `watch tree -L 2 $RIG_PATH/snakes/`; optional Pane 4 runs `po watch <epic-id>` once po-attach lands. Document recording setup (OBS / asciinema) plus the full walkthrough (provision rig → seed beads → deploy chart → dashboard.sh → dispatch epic → record) in `engdocs/snakes-demo.md`.

## Classification
- has_ui: **false** — shell-only orchestration; the Prefect UI is opened in a browser pane, not modified.
- has_backend: **true** — adds new shell-script asset under `scripts/` (routes to lint/test).
- needs_migration: **false** — no schema or DB changes.
- is_docs_only: **false** — primary deliverable is the script; docs are secondary.

## Risks / Open Questions
- WezTerm detection: rely on `command -v wezterm` plus `$WEZTERM_PANE`; need a deterministic fallback.
- tmux fallback: must work whether invoked inside an existing session or creating a fresh one; idempotent layout.
- Pane 1 spawning a browser is interactive-only — won't work headless / in CI; document as such.
- `kubectl get pods -n <ns> -w` assumes a reachable cluster context; namespace parameterized, cluster implicit.
- Optional Pane 4 (`po watch`) is gated on po-attach landing; should degrade cleanly when unavailable.
- Testing: hard to unit-test a multiplexer-spawning script; e2e bounded to `bash -n` and `--help` smoke.
- No git remote configured for this repo — script-only delivery, no PR.
