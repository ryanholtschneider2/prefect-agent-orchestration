# Epic-first PO operations TUI

**Status:** Approved design

**Date:** 2026-07-11

**Beads issue:** `prefect-orchestration-q5st`

**Audience:** PO maintainers and operators

## Summary

Replace the existing `po tui` implementation with a clean-room operations
console organized around epics and their child issues. The new interface should
feel as calm and polished as Claude Code: little chrome, strong typography and
spacing, progressive disclosure, a universal command bar, and responsive
behavior that remains usable in small terminal splits.

The TUI is a first-class companion to the CLI, not a second orchestration
system. It observes Beads, Prefect, tmux, and PO run artifacts through typed
adapters. Mutations call existing PO, Prefect, and Beads capabilities and then
verify the resulting state. It does not duplicate workflow logic or introduce
heuristic quality judgments.

## Goals

- Make all PO work visible in one epic-first hierarchy.
- Let operators understand aggregate epic progress and drill into live child
  execution without changing mental models.
- Support dispatch, pause/resume, retry, cancel, attach, artifact inspection,
  and Beads updates from a single discoverable command surface.
- Remain useful when Prefect, tmux, or another source is unavailable.
- Preserve selection, expansion, scroll position, and operator context during
  live refreshes.
- Work well at 160×48, 100×30, 80×24, and narrow tmux widths.
- Restore the terminal reliably on every exit path.

## Non-goals for the first release

- Reusing components, state, or layout code from the existing Ink TUI.
- Embedded shell emulation or a general terminal multiplexer.
- Model-authored trace summaries or recommendations.
- Heuristic stuck, risk, or quality classification.
- User-configurable widget dashboards and layouts.
- A plugin API for TUI components or actions.
- Replacing the Prefect web UI for deep orchestration administration.
- Showing every execution attempt as a navigation-tree row.

## Validated product decisions

| Decision | Choice |
|---|---|
| Primary organization | Epics with expandable child issues |
| Default scope | All epics and child issues across lifecycle states |
| Selection behavior | Epic shows aggregate progress; child shows live execution |
| Attempt representation | Issue-first; attempt history lives in child detail |
| Action scope | Full operator console |
| Action discovery | Claude Code-style fuzzy command bar |
| Visual density | Calm, minimal, progressively disclosed |
| Framework | Ink 7 on React, built and distributed with Bun |

## Experience principles

### Calm before dense

The interface should not resemble a boxed monitoring dashboard. Avoid an outer
frame, nested borders, repeated status legends, animated spinners on static
rows, and decorative gradients. Use indentation, whitespace, restrained weight,
and muted metadata to create hierarchy. A border is acceptable only where it
communicates focus or separates an overlay from underlying content.

### Stable spatial memory

The workload tree stays on the left and detail stays on the right at supported
wide sizes. Panels never reorder based on state. At narrow widths the interface
switches explicitly to a drill-down stack rather than compressing both regions
until neither is readable.

### Facts, not opinions

The UI renders declared state and relationships. It may show “Running for 42m”
or “No task update since 14:32,” but it must not label a run “stuck” based on a
hardcoded duration. If future versions offer an analysis opinion, that judgment
must come from a model, be identified as an opinion, and degrade to no opinion
when unavailable.

### Keyboard first, mouse available

Every capability is keyboard-reachable. Mouse input may select rows, toggle
disclosure, scroll, and choose command suggestions, but never gates a feature.

### Transparent mutations

Before execution, operators see what object will change and what underlying
operation will run. After execution, they see the result and verification state.

## Information architecture

The workload hierarchy is stable and work-centric:

```text
all work
├─ active
│  ├─ epic
│  │  ├─ child issue
│  │  └─ child issue
│  └─ standalone work (synthetic group)
├─ blocked
├─ failed
├─ completed
└─ archived
```

Lifecycle groups are presentation sections, not new persisted state. Epics and
issues remain the stable selectable objects. A child issue with several Prefect
attempts appears once. Its current/latest attempt is summarized on the row; its
complete attempt history is shown in detail.

Standalone issues and unattributed Prefect runs must remain visible rather than
being silently discarded. Put standalone issues under a clearly labeled
synthetic group. Put unresolved execution records in a diagnostics view with
the identifiers needed to repair their metadata.

### Epic row

An epic row should expose only what is needed for scanning:

- disclosure marker and state glyph;
- epic ID and title;
- compact child roll-up, such as `7/12 complete · 2 running · 1 blocked`;
- most recent activity age.

### Child issue row

A child row should show:

- issue state glyph;
- short ID and title;
- current role or latest outcome;
- elapsed time while active;
- attempt count only when greater than one.

Columns truncate rather than wrap. Titles consume flexible remaining width.
Full values are available in detail.

## Primary layout

```text
 EPICS / CHILD ISSUES                 SELECTED CONTEXT
 ──────────────────────────────────────────────────────────────
 ▾ Formula graph migration           Epic overview
   ● Replace verdict channel          Progress and outcomes
   ◌ Migrate role prompts             Dependencies and blockers
   × Remove legacy scaffolding        Active agents / recent activity

 ▸ Account fallback chains

 > Type a command…                    scope: all  sources: healthy
```

The left region uses approximately 35% of wide terminals. The right region is
adaptive rather than a fixed dashboard of widgets.

### Epic detail

Selecting an epic shows:

1. completion roll-up and declared lifecycle state;
2. child outcomes grouped by state;
3. dependency graph or compact dependency list;
4. explicit blockers and pending human decisions;
5. active child runs and agents;
6. recent activity and produced artifacts.

### Child issue detail

Selecting a child issue shows:

1. issue summary and Beads state;
2. current/latest attempt header;
3. role timeline with task state, model/runtime facts, and iteration count;
4. live agent output when a tmux session exists;
5. attempt history;
6. artifacts, description, dependencies, and activity as secondary views.

The primary content changes with selection. Secondary views use a lightweight
tab strip or command navigation; they do not all remain on screen at once.

## Navigation and input

Normal navigation uses a deliberately small vocabulary:

| Key | Action |
|---|---|
| `↑` / `↓`, `j` / `k` | Move selection |
| `←` / `→`, `h` / `l` | Collapse / expand |
| `Enter` | Open primary detail or drill down |
| `/` or `:` | Open command bar |
| `Esc` | Cancel overlay/input or navigate back |
| `?` | Contextual help |
| `q` | Quit when no overlay or input is active |

`Ctrl+C`, `Ctrl+Z`, and terminal flow-control keys are not application
bindings. Signal handling remains conventional.

## Command bar

The command bar is the universal action surface and the visual anchor at the
bottom of the screen. Opening it presents fuzzy-ranked commands applicable to
the current selection, followed by global commands. Operators should not need
to memorize action keys.

Example contextual commands:

- `Dispatch child issue…`
- `Retry latest attempt`
- `Pause epic…`
- `Resume paused run`
- `Cancel current attempt…`
- `Attach to active agent`
- `Open Prefect run`
- `Open artifact…`
- `Update issue state…`
- `Add Beads comment…`
- `Refresh all sources`
- `Open source diagnostics`
- `Change scope…`

The palette uses smart-case matching and always shows the current result count.
Commands define a title, aliases, applicable selection types, argument schema,
preview builder, confirmation policy, executor, and verification query.

Structured arguments appear as compact steps inside the command area. Dispatch
collects the formula and explicit runtime tuple required by repository policy:
backend, provider/account, account class, model, effort, rig, and rig path. It
pre-populates known defaults but never hides the final dispatch preview.

## Mutation lifecycle

Every mutation follows the same state machine:

```text
select command
  → collect required arguments
  → render concrete preview
  → confirm when required
  → execute asynchronously
  → re-read relevant source
  → report verified, pending, or failed
```

Destructive commands default to cancellation. Confirmations must name the
affected object and consequence; never use a bare “Are you sure?” Bulk actions
show their exact target count and offer a review list before confirmation.

While an identical operation is in flight, duplicate submission is disabled.
A successful process exit is not sufficient proof of completion. The action
result remains “verification pending” until the source reflects the requested
state or a bounded verification window expires.

The activity view records timestamp, selected object, exact command or API
operation, exit/result state, and verification result. Secrets and credentials
must be redacted before persistence or display.

## Architecture

The new implementation is isolated from the legacy TUI:

```text
Beads + Prefect + tmux + run artifacts
                  ↓
         typed source adapters
                  ↓
        normalized operations model
                  ↓
       reactive store → Ink views
                  ↓
        confirmed action executor
```

Suggested source layout:

```text
tui-next/
├─ src/
│  ├─ app/             # root app, screen modes, lifecycle
│  ├─ domain/          # normalized types and reconciliation
│  ├─ sources/         # beads, prefect, tmux, artifacts adapters
│  ├─ actions/         # registry, schemas, previews, executors
│  ├─ state/           # store, selectors, refresh coordination
│  ├─ components/      # tree, details, command bar, overlays
│  ├─ theme/           # semantic tokens and terminal capability mapping
│  └─ cli.tsx          # argument parsing and TTY/plain-mode selection
└─ test/
   ├─ fixtures/
   ├─ unit/
   ├─ render/
   └─ integration/
```

The temporary directory name keeps the clean-room build honest. At cutover,
replace `tui/` atomically or update the launcher/build path in one reviewed
change. Do not maintain two production TUIs.

## Normalized domain model

The UI depends on stable domain objects rather than rendering raw API records.
The initial model should include:

- `Epic`: ID, title, Beads state, children, declared dependencies, timestamps.
- `Issue`: ID, epic ID, title, Beads state, dependencies, attempts, artifacts.
- `Attempt`: Prefect flow-run ID, formula, state, timestamps, tags, runtime tuple.
- `RoleExecution`: task-run ID, role/task name, state, iteration, timestamps.
- `AgentSession`: tmux/session identity, role, availability, captured-output cursor.
- `Artifact`: name, type, path or URL, producer, creation time.
- `SourceSnapshot<T>`: data, fetch time, freshness, error, retry state.
- `ActivityRecord`: action request, execution result, verification result.

Join records only through stable identifiers, Prefect tags, declared parentage,
and existing run metadata. Do not match records by similar titles or inferred
timing. When a relationship cannot be resolved, retain the record in an
unattributed diagnostics collection.

## Source adapters

### Beads

Beads is authoritative for epics, child issues, dependencies, descriptions,
comments, and work state. The adapter must support the repository’s selected
backend through existing PO/Beads seams rather than assuming raw `bd` behavior.
Read operations should prefer structured JSON.

### Prefect

Prefect is authoritative for flow attempts, task runs, orchestration state,
timing, retries, and deployment identity. Query flow runs in bounded pages and
fetch task runs only for visible or selected work when scale requires it.

### tmux

tmux is authoritative only for local live-session availability and captured
terminal output. Missing sessions are normal, not exceptional. Output capture
must be incremental or content-addressed so unchanged panes do not trigger
redraws.

### Run artifacts

PO run directories are authoritative for artifacts and runtime metadata written
there. File reads occur off the render path and watch only selected/visible
work. Artifact viewers should open externally when content is unsuitable for
terminal rendering.

## Refresh and state management

Each source owns an independent refresh loop and publishes timestamped
snapshots. A slow or failing source cannot block the others. Source intervals
should reflect data behavior: workload metadata can refresh less frequently
than selected live output.

Use event-driven refresh after local actions and modest polling at external
boundaries. Do not repaint on a fixed animation timer. Store updates should use
structural equality and content hashes so unchanged snapshots do not rerender
large trees or live-output panes.

Persist only lightweight operator preferences and navigation context, such as
last scope and theme. Runtime source data stays ephemeral or in a bounded cache.

The store must preserve:

- selected epic/issue and fallback selection if it disappears;
- expanded epic IDs;
- scroll offsets per view;
- active detail subview;
- command input and pending confirmation;
- source freshness and errors;
- in-flight actions and activity history.

## Error handling and degraded operation

There is no global all-or-nothing load state. Each adapter reports health,
freshness, and last success independently.

- Prefect unavailable: render the Beads hierarchy and mark execution facts
  stale or unavailable.
- Beads unavailable: retain the last hierarchy snapshot and expose raw Prefect
  runs through diagnostics.
- tmux session missing: show “No active agent session” while preserving attempt
  history and artifacts.
- artifact read failure: show the path, failure, and retry/open-externally
  options without disturbing navigation.

Refresh failures back off exponentially with bounded jitter. Manual “Retry now”
is always available. Successful adapters continue normally. A quiet footer
health indicator opens a diagnostics view containing operation, endpoint or
command, exit status, concise stderr, and last successful refresh.

Application logs go to the user cache directory and never stdout while the TUI
owns the screen. The diagnostics view links the active log path.

## Responsive behavior

### Wide: 100 columns and above

Show the stable two-region layout. Secondary metadata may be visible beside
primary values when space permits.

### Compact: 80–99 columns

Keep two regions but reduce the tree width, hide secondary columns, shorten
labels, and use compact detail navigation. All hidden data remains accessible
through detail views.

### Narrow: approximately 60–79 columns

Switch to a drill-down stack. The workload tree is the home screen. `Enter`
opens the selected object’s detail; `Esc` returns. The command bar and source
health remain visible.

### Below viable minimum

Render a plain, centered explanation with the current and required dimensions.
Never attempt a compressed multi-pane layout or crash on resize.

Breakpoints must be validated empirically with the chosen Ink version and
terminal-width calculations. Cell width, not JavaScript string length, governs
truncation.

## Visual system

Define semantic tokens rather than scattering color values:

- `text.primary`, `text.muted`, `text.disabled`;
- `surface.selection`, `surface.overlay`;
- `accent.primary`, `accent.focus`;
- `status.running`, `status.success`, `status.warning`, `status.error`;
- `border.subtle`, `border.focus`.

The default theme should inherit the terminal background and use one restrained
accent family. State colors follow terminal conventions, but every state also
has a glyph or label. Selection uses reverse video or an adaptive background so
it remains visible under `NO_COLOR=1`.

Respect `NO_COLOR`, detect terminal color capability, and provide ASCII glyph
fallbacks. Bold is reserved for current selection, primary titles, and command
matches. Dim styling is for metadata. Avoid italics as the only distinction.

The initial release may ship a dark adaptive theme and monochrome mode. A theme
marketplace is unnecessary.

## Terminal lifecycle

Use Ink 7’s alternate-screen support. Restore raw mode, cursor visibility,
mouse capture, keyboard protocol state, and the primary screen on:

- normal quit;
- `SIGINT` and `SIGTERM`;
- uncaught exceptions and rejected promises;
- suspend (`SIGTSTP`) before yielding to the shell;
- process resume (`SIGCONT`) followed by a full redraw.

Do not bind `Ctrl+C`, `Ctrl+Z`, `Ctrl+\`, `Ctrl+S`, or `Ctrl+Q` to application
commands. Non-TTY and CI invocation falls back to a concise plain-text status
summary or a clear message, depending on arguments.

## Performance expectations

- Navigation and command filtering should respond within one rendered frame.
- No filesystem, HTTP, or subprocess operation runs on the render path.
- Large issue collections use windowed rendering or a bounded visible slice.
- tmux and artifact output panes render only visible lines.
- Unchanged source snapshots produce no meaningful repaint.
- Live output refresh preserves user scroll; follow mode resumes only on an
  explicit jump-to-bottom action.

The application should idle without redrawing when no source or input changes.

## Testing strategy

### Domain unit tests

Test normalization, stable joins, grouping, selection fallback, roll-ups, and
command applicability using fixtures for:

- multiple rigs and lifecycle states;
- nested epic children and dependency edges;
- standalone issues and unattributed runs;
- repeated attempts and role iterations;
- missing tags and partial runtime metadata;
- stale or failed source snapshots.

Any aggregate shown in the UI must derive mechanically from declared states and
have direct tests.

### Rendering tests

Use Ink rendering snapshots at 160×48, 100×30, 80×24, 60×24, and below minimum.
Cover:

- expanded and collapsed epic trees;
- epic and child detail;
- long Unicode titles and truncation;
- empty, loading, stale, and partial-source states;
- command results, argument prompts, and confirmations;
- monochrome and reduced-color output.

Snapshots are review aids, not the sole functional tests.

### Interaction tests

Drive the real component tree through keyboard input. Verify navigation,
expansion, drill-down/back behavior, palette filtering, argument entry,
confirmation cancellation, selection persistence, and scroll preservation after
refresh.

### Adapter integration tests

Run against fake Prefect HTTP and subprocess boundaries. Exercise structured
parsing, pagination, cancellation, timeouts, non-zero exits, partial data, and
redaction. Do not globally mock internal modules; mock system boundaries.

### PTY end-to-end tests

Launch the compiled binary in a pseudo-terminal with fixture sources. Verify:

- alternate-screen entry and restoration;
- resize transitions at every breakpoint;
- clean `Ctrl+C` and supported suspend/resume;
- non-blocking behavior during slow source calls;
- mutation preview, execution, and verification;
- plain output under non-TTY invocation.

### Real-stack smoke test

Run read-only against this repository’s current Beads database and local
Prefect service. Separately execute a bounded mutation smoke against disposable
fixture work. Capture terminal screenshots or SVG frames as the reviewer-facing
verification artifact.

## Delivery plan

Implementation should proceed in vertical slices rather than building every
adapter before anything is visible:

1. **Foundation:** new package, terminal lifecycle, semantic theme, responsive
   shell, fixture data, and PTY harness.
2. **Workload browser:** Beads adapter, epic/issue model, tree navigation, epic
   detail, source health.
3. **Execution detail:** Prefect attempts and task runs, adaptive child detail,
   attempt history.
4. **Live agents:** tmux session mapping and stable incremental output.
5. **Command surface:** palette framework, activity records, inspect/open/attach
   actions.
6. **Mutations:** dispatch, retry, pause/resume, cancel, and Beads updates with
   previews and verification.
7. **Artifacts and polish:** artifact browsing, degraded states, performance,
   accessibility, screenshots, and documentation.
8. **Cutover:** make the new binary the sole `po tui`, remove legacy source,
   close or migrate obsolete TUI issues, and run the full quality gate.

Each slice must end with a runnable artifact and tests at the relevant terminal
sizes. Do not postpone terminal cleanup, resize behavior, or source-failure
handling until the polish phase.

## Acceptance criteria

The first release is complete when:

- `po tui` opens the complete epic-first workload and expands child issues.
- Selecting an epic shows aggregate progress, dependencies, blockers, active
  work, and recent outcomes.
- Selecting a child shows current execution, role timeline, live output when
  available, attempt history, and artifacts.
- The command bar discovers and performs the approved operator actions.
- Destructive and bulk actions show concrete previews and are verified after
  execution.
- Loss of Prefect, Beads, tmux, or artifact access degrades only the affected
  parts of the interface.
- Selection and scroll context survive background refresh.
- The UI is usable at 80×24 and switches to drill-down at narrow widths.
- `NO_COLOR=1`, non-TTY output, Unicode width, and ASCII fallbacks work.
- Normal exit, signals, exceptions, suspend, and resume restore the terminal.
- Unit, render, interaction, adapter, PTY, and real-stack smoke tests pass.
- The old TUI implementation is no longer in the production path.

## Open implementation details

The following decisions are intentionally left to implementation spikes because
they have one technically correct answer only after measurement:

- exact compact/narrow breakpoint columns;
- whether the store uses Zustand or a reducer-based external store;
- the windowing mechanism for very large trees;
- Prefect query batch size and adapter refresh intervals;
- whether production output is a Bun-compiled binary or a packaged runtime once
  Ink 7 compatibility is verified.

These choices must preserve the architecture and acceptance criteria above.
They do not require reopening product design unless a spike reveals a user-
visible trade-off.
