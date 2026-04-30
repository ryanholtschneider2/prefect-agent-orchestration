# po-tui

Live, issue-centric TUI for the `po` Prefect-orchestration swarm.

Same aesthetic as Claude Code (Ink + React 18). Bun is the runtime and
the bundler; the production build is a single self-contained binary.

## Layout

```
┌─ po · <epic-id or "all"> ─────────────────────  running:N  done:N  failed:N ─┐
│ ISSUES                  │ ROLE TIMELINE — <selected issue>                   │
│ ────────────────────────┤ triage ✓  plan ✓  critique ⟲2  build ▶  lint ·  │
│ ▶ 4ja.1  build  ▶▶▶    │ ──────────────────────────────────────────────────│
│   4ja.3  plan   ⟲       │ TMUX TAIL — po-4ja.1-build  (or BD SHOW — <id>)    │
│   4ja.4  baseline ✗    │ <last 30 lines>                                    │
└─────────────────────────┴────────────────────────────────────────────────────┘
 [↑↓] nav  [a] attach  [r] refresh  [/] filter  [b] bd-show  [q] quit
```

The right panel's bottom slot toggles between the live tmux tail (default)
and a `bd show <id>` pane via the `b` key. The bd-show pane renders the
selected issue's bd metadata, description, and parent-child children — handy
for reading the user's task description, surveying an epic's children, or
inspecting a closed bead's `close_reason` without leaving the TUI.

## Data sources

All read-only, polled every `--refresh-ms` (default 2000):

1. **Prefect REST** at `http://127.0.0.1:4200/api`
   - `POST /flow_runs/filter` — filtered by tags `issue_id:*` / `epic_id:*`
   - `POST /task_runs/filter` — per flow run, derives the role->state map
2. **`bd list --json`** / **`bd show <id> --json`** via `Bun.spawn` — title,
   bd status, dependencies, plus (for `show`) full description, metadata,
   `dependents`, and `close_reason`. `bdShow()` unwraps the single-element
   array `bd` returns and throws on shellout failure / bad JSON; the store
   catches and renders a "stale" overlay on top of the last cached value.
   `bd list` failure is non-fatal.
3. **`tmux capture-pane -t po-<issue>-<role> -p -S -200`** — the live tail
   of the active role's pane.

## Develop

```bash
cd prefect-orchestration/tui
bun --version          # require: ≥ 1.1
bun install
bun run dev            # runs src/cli.tsx in-place
bun run typecheck      # tsc --noEmit, strict
```

If `bun: command not found`, install Bun first
(<https://bun.sh/docs/installation>) — this scaffold does not auto-install.

## Build the binary

```bash
bun run build
# → ./dist/po-tui   (a single self-contained executable)
```

The exact command:

```bash
bun build --compile --target=bun-linux-x64 src/cli.tsx --outfile dist/po-tui
```

To wire it into the `po` CLI lookup path, copy or symlink the binary to
`prefect-orchestration/bin/po-tui`:

```bash
mkdir -p ../bin
cp dist/po-tui ../bin/po-tui
```

`po tui` (the Python CLI subcommand) searches in this order:
1. `prefect-orchestration/bin/po-tui`
2. `prefect-orchestration/tui/dist/po-tui`
3. `po-tui` on `$PATH`

If none of those exist it prints a friendly hint pointing back here.

## CLI args

| flag | default | purpose |
|---|---|---|
| `--epic <id>` | (none — show all) | filter to a single epic via `epic_id:<id>` Prefect tag |
| `--prefect-url <url>` | `$PREFECT_API_URL` or `http://127.0.0.1:4200/api` | API base |
| `--refresh-ms <n>` | `2000` | poll cadence |

## Hotkeys

- `↑` / `↓` — navigate the issue list (siblings of selected row)
- `←` / `→` — jump to parent / drill into first child
- `a` — open `tmux attach` in a new tab/window for the active role
- `A` — exit cleanly and `tmux attach -t <session>` in-place
- `r` — force a refresh tick
- `/` — filter issues by id or title substring
- `e` / `E` — drill into the selected row's parent / pop back out
- `t` — hide terminal-state rows (show only running/queued/paused)
- `b` — toggle the right panel's bottom slot between TMUX TAIL and BD SHOW
- `j` / `k` — scroll the BD SHOW pane down/up by one line (only while visible)
- `g` / `G` — jump BD SHOW pane to top/bottom (only while visible)
- `q` — quit

## Status

This is a scaffold — layout, data wiring, and build pipeline are solid.
UI polish (colors, table density, sparklines, role-graph from
`graph-v2`) iterates from here.
