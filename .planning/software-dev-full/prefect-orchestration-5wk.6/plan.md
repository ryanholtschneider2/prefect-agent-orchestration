# Plan — prefect-orchestration-5wk.6

## Affected files

- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/scripts/snakes-demo/dashboard.sh` (new, +x)
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/engdocs/snakes-demo.md` (new)

## Approach

Single self-contained bash script that lays out a recording dashboard for the snakes
fanout demo. Two layout backends, picked at runtime:

1. **WezTerm** — preferred when `command -v wezterm` succeeds AND `WEZTERM_PANE` is
   set (i.e. we're already running inside a WezTerm pane, which is required for
   `wezterm cli spawn` to attach panes to the current tab). Use `wezterm cli
   split-pane --right`/`--bottom --percent N -- bash -c '<cmd>; exec bash'`. The
   `exec bash` trailer keeps panes alive so the recording captures the final state.
2. **tmux** — fallback. If `$TMUX` is set, split the current window in place; else
   `tmux new-session -d -s snakes-demo` + splits, then `tmux attach`. Use
   `select-layout tiled` so the four panes are predictable.

Pane commands (parameterized):

- Pane 1: `xdg-open "http://localhost:4200/runs?tag=epic:${EPIC_ID}"` (or just
  `firefox` if `xdg-open` missing); falls back to printing the URL when no
  display. Browser is launched once, then the pane runs `bash` so it doesn't die.
- Pane 2: `kubectl get pods -n "$NAMESPACE" -w`
- Pane 3: `watch -n 1 "tree -L 2 '$RIG_PATH/snakes/' 2>/dev/null || ls -la '$RIG_PATH/snakes/'"`
- Pane 4 (optional, only when `--epic-id` supplied AND `po watch --help` exits 0):
  `po watch "$EPIC_ID"`. Skip silently when `po watch` is missing (`po-attach`
  hasn't landed) — log "skipping pane 4: po watch unavailable" to stderr.

Flags (parsed with a small `while [[ $# -gt 0 ]]` loop, no `getopts` so long flags
work cleanly):

- `--rig-path PATH` (required) — used by Pane 3 and exported as `RIG_PATH`.
- `--namespace NS` (default `po`) — used by Pane 2.
- `--epic-id ID` (optional) — enables Pane 4 and the Prefect-UI tag filter.
- `--layout wezterm|tmux|auto` (default `auto`) — force a backend, mostly for tests.
- `--dry-run` — print the chosen backend + the command it would run for each pane
  to stdout, exit 0. Lets us smoke the script without spawning anything.
- `--help` / `-h` — usage text + exit 0.

Script contract: `set -euo pipefail`; rejects missing `--rig-path` with usage +
exit 2; rejects unknown layout with exit 2; uses `printf` (no `echo -e`); quotes
every variable expansion that lands in a pane command. Shebang `#!/usr/bin/env bash`.

`engdocs/snakes-demo.md` walkthrough sections (linear, copy-pasteable):

1. **Provision rig** — `mkdir snakes-rig && (cd snakes-rig && bd init --server …)`
2. **Seed beads** — example `bd create` for an epic + N children with `bd dep add`.
3. **Deploy chart** — `helm install po charts/po -n po --create-namespace …`.
4. **Launch dashboard** — `scripts/snakes-demo/dashboard.sh --rig-path $PWD/snakes-rig --namespace po --epic-id <id>`.
5. **Dispatch epic** — `po run epic --epic-id <id> --rig snakes --rig-path $PWD/snakes-rig`.
6. **Record** — OBS scene capturing the WezTerm/tmux window; asciinema alternative
   for terminal-only capture (`asciinema rec snakes.cast`); note that pane 1
   (browser) needs OBS — asciinema is terminal-only.

## Acceptance criteria (verbatim from issue)

- Script works in WezTerm and tmux fallback.
- `engdocs/snakes-demo.md` walkthrough: provision rig → seed beads → deploy chart
  → dashboard.sh → dispatch epic → record.

## Verification strategy

- **AC1 (script works in both backends)** — verified via `--dry-run`:
  - `bash -n scripts/snakes-demo/dashboard.sh` (syntax)
  - `scripts/snakes-demo/dashboard.sh --help` exit 0, prints usage
  - `scripts/snakes-demo/dashboard.sh --rig-path /tmp --layout wezterm --dry-run`
    prints `backend=wezterm` + 3 (or 4) `wezterm cli split-pane …` commands
  - `scripts/snakes-demo/dashboard.sh --rig-path /tmp --layout tmux --dry-run`
    prints `backend=tmux` + the corresponding `tmux split-window …` commands
  - `scripts/snakes-demo/dashboard.sh --rig-path /tmp --layout tmux --dry-run --epic-id e1`
    includes a `po watch e1` line in the output (or a "skipping pane 4" notice
    when `po watch` is unavailable on the host).
  - Live tmux smoke (manual): `tmux new -d -s t && scripts/snakes-demo/dashboard.sh
    --rig-path /tmp --layout tmux` results in 3+ panes (`tmux list-panes -t t | wc -l`).
- **AC2 (walkthrough doc)** — manual: `engdocs/snakes-demo.md` contains the six
  numbered sections in order. Verified by `grep -E '^## ' engdocs/snakes-demo.md`
  matching the expected headings.

## Test plan

- **unit** — none. The script is dispatch glue with no Python; mocking
  `wezterm`/`tmux`/`kubectl` to assert string equality would just re-encode the
  source.
- **e2e** — none beyond the `--dry-run` smoke above; running real `wezterm cli
  spawn` / `tmux new-session` inside CI is brittle and out of scope per triage
  ("hard to unit-test a multiplexer-spawning script").
- **playwright** — N/A (no PO UI; the Prefect UI is opened in a browser pane but
  is not modified).

The two `--dry-run` checks above are the regression-gate's contract for this
issue. Run them from the build step and from `regression-gate` so a future edit
to the script's flag-parsing trips them.

## Risks

- **WezTerm pane attach** — `wezterm cli spawn` only attaches to the current tab
  when invoked from inside a WezTerm pane. If a user runs the script from a
  non-WezTerm terminal but has the `wezterm` binary installed, naive detection
  would route to WezTerm and fail. Mitigation: require both `command -v wezterm`
  AND `$WEZTERM_PANE` to choose the WezTerm backend.
- **Browser pane in CI / headless** — Pane 1 launches a GUI browser; on a
  headless host `xdg-open` may exit non-zero or hang. Mitigation: best-effort
  `xdg-open … || printf 'open: %s\n' "$URL"`, never fail the script over it,
  document the limitation.
- **`po watch` not yet shipped** — Pane 4 is gated behind `po watch --help`
  exiting 0; degrade silently when missing so the script is useful today.
- **No git remote** — local-only delivery; no PR. Just commit + close the bead.
- **No API or schema changes** — no migration / breaking-consumer risk.
