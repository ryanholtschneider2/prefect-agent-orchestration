# po-tui

The production `po tui` is an epic-first operations console built with Ink 7,
React 19, and Bun. It keeps Beads work as the navigation model while joining
Prefect attempts, tmux sessions, and run artifacts through stable identifiers.
Each source degrades independently; a missing Prefect service never hides the
Beads hierarchy.

## Layout and breakpoints

```text
PO  epic operations  scope: all  sources: +b +p +t +a

ACTIVE
▾ ◆ Formula graph migration  1/3 · 1 run
  └ ◆ Replace verdict channel        builder 4m  │ EPIC / ISSUE DETAIL
  └ ○ Migrate role prompts                    2h │ progress, dependencies,
                                                  │ attempts, roles, artifacts
> Type a command…
```

At 100 columns and above the hierarchy and detail remain side by side. At
80–99 columns secondary row metadata collapses. At 60–79 columns the hierarchy
becomes a drill-down stack (`Enter` opens detail and `Esc` returns). Below
56×18 the app renders a clear size explanation instead of compressing.

The command bar (`:` or `/`) fuzzy-ranks contextual actions followed by global
actions. Commands declare structured arguments. Dispatch collects formula,
backend, provider, account, account class, model, effort, rig, and rig path;
state/comment commands collect their values rather than reading hidden defaults.
Pause and resume use Prefect's supported orchestration REST endpoints and are
offered only for compatible run states; cancel continues through the supported
`prefect flow-run cancel` command. Artifact and attach actions require a choice
from the paths and tmux sessions discovered by the active source snapshots.
The final preview names the target and exact transport command. Non-destructive
mutations execute from that preview. Destructive actions default-cancel and
require typing the selected object ID. Identical in-flight operations are
suppressed. After execution, the console re-reads Beads or Prefect for a bounded
window and records `verified`, `pending` (window expired), or `failed`.

## Detail views

| View | Content | Source |
|---|---|---|
| overview | Epic roll-up or current issue execution | Beads + Prefect |
| activity | Exact local operations and verification | action executor |
| artifacts | Produced evidence | PO run directories |
| description | Full issue description | Beads |

`Tab` cycles the detail view. Epics expose progress, dependencies, blockers,
active children, and recent outcomes. Issues expose the latest attempt, runtime
tuple, role timeline, attempt history, dependencies, and artifacts.

## Data sources and degraded operation

Each adapter owns an independent refresh controller; a slow source never delays
another source. Controllers use per-source intervals, abortable timeouts,
bounded exponential backoff/jitter, content hashes to avoid unchanged renders,
and last-good snapshots for stale operation:

1. **Beads** — epics, child relationships, dependencies, descriptions, state.
2. **Prefect REST** — flow attempts and task/role execution.
3. **tmux** — local session availability and bounded captured output only.
4. **run artifacts** — bounded discovery below the rig's `.planning/` tree.

Press `:` and choose “Open source diagnostics” to see operation/endpoint or
command, freshness, last success, retry attempt/next time, exit status, redacted
stderr, unresolved stable IDs, and log path. `r` retries every controller now.
`NO_COLOR=1` and `--ascii` preserve all state information. Non-TTY/CI invocation
automatically prints a concise plain summary.

## Develop and verify

```bash
cd tui
bun install
bun run typecheck
bun test
bun run build
uv run python test/pty_smoke.py
./dist/po-tui --plain --rig-path ..
```

The production build is `tui/tui-next/src/cli.tsx`; legacy `tui/src` files are
not imported or compiled. `po tui update` compiles the Bun binary and atomically
copies it to `bin/po-tui`, which remains the public launcher path.

## Options

| Flag | Default | Purpose |
|---|---|---|
| `--rig-path` | current directory | Beads and run-artifact root |
| `--prefect-url` | `$PREFECT_API_URL` or local API | Prefect API base |
| `--refresh-ms` | `5000` | external-source polling cadence |
| `--ascii` | false | ASCII state/disclosure glyphs |
| `--plain` | auto for non-TTY | concise non-interactive summary |

## Keys

- `↑`/`↓` or `j`/`k`: move selection
- `←`/`→` or `h`/`l`: collapse/expand an epic
- `Enter`: open narrow detail or confirm a command
- `Esc`: cancel or return from narrow detail
- `/` or `:`: fuzzy command bar with all operator actions
- `Tab`: cycle detail views
- `PageUp`/`PageDown`: scroll detail; `J`/`K`: scroll live output; `G`: resume follow
- `r`: refresh all sources; `?`: help; `q`: quit

Terminal-owned controls (`Ctrl+C`, `Ctrl+Z`, `Ctrl+\`, `Ctrl+S`, `Ctrl+Q`) are
not application bindings. Alternate-screen, cursor, suspend/resume, resize,
SIGINT/SIGTERM, exception/rejection, and attach-handoff cleanup are handled by
the entry point. `test/pty_smoke.py` verifies every path against the compiled
binary in a real pseudo-terminal.
