# po-tui

Live, issue-centric TUI for the `po` Prefect-orchestration swarm.

Same aesthetic as Claude Code (Ink + React 18). Bun is the runtime and
the bundler; the production build is a single self-contained binary.

## Layout

```
┌─ po · <epic or "all">    run:N  stuck:N  ok:N  fail:N ─┬──────────────────────┐
│ ISSUES (running on top, done collapsed below ─)        │ DETAIL: <selected>   │
│ ▶ 5vh  fast    4m   build ⟲1     Add overview         │ [LIVE][TRACE][BD][ACT]│
│   ld9  full   52m   plan  ⟲2  ⚠  IssueList rows       ├──────────────────────┤
│   1qn  full   48m   review ⟲3   RoleTL+tmux fix       │ ━━ LIVE ━━━━━━━━━━━━ │
│   3ti  full  1h7m   build ⟲1                          │ planner✓ → builder⟳ │
│ ─                                                       │ tmux tail (last 30): │
│   oiu  fast    ✓5m  graph_run fix                      │   <live agent output>│
└────────────────────────────────────────────────────────┴──────────────────────┘
 [↑↓] sibs  [←→] tree  [t] tab  [1-4] jump  [c] cancel  [r] retry  [d] dispatch
 [D] show-done  [a] attach  [A] in-place  [/] filter  [e/E] drill  [q] quit
```

The right pane is a 4-tab detail view, default tab **LIVE**:

| Tab | Content | Source |
|---|---|---|
| LIVE | RoleTimeline + tmux tail of the active role | tmux + Prefect |
| TRACE | placeholder (`coming soon — see prefect-orchestration-qhg`) | — |
| BD | `bd show <id>` — description, metadata, dependents, `close_reason` | `bd show --json` |
| ACTIONS | static keybind reference panel | — |

Per-row columns: `<id>  <flow-mode>  <wall>  <step+iter>  <stuck>  <title>`.
`flow-mode` is `fast`/`full`; `wall` humanizes (`4m` / `1h7m`, red over 1h);
`stuck` (`⚠`) flags rows whose wall exceeds 2× the step-typical heuristic
(see `STEP_TYPICAL_MS` in `state/store.ts`). Done rows collapse below an `─`
separator (toggled by `D`); the header counts running / stuck / ok / fail.

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

Navigation:
- `↑` / `↓` — siblings of the selected row
- `←` / `→` — parent / first child
- `e` / `E` — drill into selection's parent / pop back out
- `/` — filter issues by id or title substring

Tabs (right pane):
- `t` — cycle tabs (LIVE → TRACE → BD → ACTIONS)
- `1` / `2` / `3` / `4` — jump to LIVE / TRACE / BD / ACTIONS
- `j` / `k` / `g` / `G` — scroll the BD tab (active only while BD is selected)

Actions on the selected run:
- `c` — cancel (confirm overlay; shells `prefect flow-run cancel <flow_run_id>`)
- `r` — retry (confirm overlay; shells `po retry <issue_id>`)
- `d` — dispatch a new run (multi-step form: issue id → flow → rig → rig-path)
- `o` — open the flow run in the Prefect UI (`xdg-open`)
- `D` — toggle show-done (collapse / expand the done block)
- `a` — `tmux attach` to the active role's pane in a new terminal window
- `A` — exit cleanly and `tmux attach -t <session>` in-place
- `q` — quit

## Status

This is a scaffold — layout, data wiring, and build pipeline are solid.
UI polish (colors, table density, sparklines, role-graph from
`graph-v2`) iterates from here.
