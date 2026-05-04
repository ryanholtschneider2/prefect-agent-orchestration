---
name: po
description: Dispatch beads issues and epics for autonomous implementation via the po CLI — actor-critic pipeline orchestrated by Prefect with lurkable tmux agent sessions.
---

# PO — Prefect Orchestration for autonomous bead work

> **Skill status**: see [`reports/latest.md`](reports/latest.md) for the
> latest eval pass rate. Run `po run skill-evals --pack
> prefect-orchestration --skill po` to refresh.

`po` is a CLI (installed globally via `po packs install` / `uv tool install`) that dispatches
beads issues to an actor-critic pipeline and dispatches beads epics as
DAG-ordered fan-outs. Each role in the pipeline (triager, planner,
critic, builder, verifier, …) runs as a Claude subprocess inside a
named tmux session so you can `tmux attach` and watch live.

Use this skill whenever you have a beads issue ready for
implementation and want it worked autonomously, OR an in-progress
epic whose ready children should fan out concurrently.

## Roadmap-first planning workflow

Start at the highest useful planning layer before dispatching implementation:

1. Goal or roadmap discussion
2. Durable planning artifact under `.planning/products/<slug>/` or `.planning/epics/<slug>/`
3. Beads epic and child issue creation via `bd`
4. Inline vs subagent vs `po` dispatch choice

Use the scaffold command when the durable artifact does not exist yet:

```bash
po planning-init --kind=product --slug=<product-slug> --title="Product Name"
po planning-init --kind=epic --slug=<epic-slug> --title="Epic Name"
```

Artifact layout:

- Product planning: `.planning/products/<slug>/<slug>-vision.md` and `<slug>-epics.md`
- Epic planning: `.planning/epics/<slug>/<slug>-brainstorm.md`, `<slug>-design.md`, `<slug>-epic-plan.md`, and `<slug>-issues.md`

After the artifact exists, refine it with the user, then translate it into `bd`
epics and child beads. Only dispatch `po run epic` or `po run software-dev-full`
after the work has been decomposed enough that a verifier can judge success.

## Choosing PO vs Inline vs Subagents

- Do it inline for trivial edits, tiny config changes, or one-file fixes.
- Use local subagents for bounded research or narrow edits where you do not need persistent verification artifacts.
- Use `po` when the work should run unattended, needs verifier/critic gates, spans multiple beads, or benefits from durable run artifacts and resumability.

Rule of thumb: if manual re-verification would be the expensive part, prefer `po`.

## Prerequisites

```bash
# One-time: install po CLI globally (already done on this machine).
# Bootstrap core via uv, then add packs with `po packs install` (which thinly
# wraps `uv tool install --force` so entry-point metadata is refreshed):
uv tool install --editable /path/to/prefect-orchestration
po packs install --editable /path/to/po-formulas

# Other pack-lifecycle subcommands:
po packs install <pack>      # from PyPI
po packs install <git-url>   # from git
po packs update [<pack>]     # refresh EP metadata (all packs if no arg)
po packs uninstall <pack>
po packs list                # table of installed packs + what each contributes

# Must be running before po run: Prefect server (provides UI + state)
prefect server start --host 127.0.0.1 --port 4200 &
# UI: http://127.0.0.1:4200
# If omitted, each `po run` spins up its own ephemeral server on a
# random port — UI is still there, just per-run.

# Per-rig: bd must be initialized (usually already is)
#   cd <rig-path> && bd init    # if not
#   prefer backend=dolt-server over embedded for parallel runs

# Optional: tmux on PATH — enables lurkable agent sessions (default
# backend). If missing, falls back to subprocess pipes (no lurking).

# Optional: prefect worker — only needed for scheduled deployments
prefect worker start --pool po &
```

## Core verbs

```bash
po list                              # every formula registered by installed packs
po show <formula>                    # signature + docstring
po run <formula> --args              # run a formula synchronously, in-process
po deploy                            # list pack-declared deployments
po deploy --apply                    # upsert to Prefect server
po doctor                            # health check: bd, prefect, pools, entry points
po <command>                         # pack-shipped utility command (see po.commands)
```

## Dispatching a single beads issue for autonomous implementation

```bash
po run software-dev-full \
  --issue-id <bead-id> \
  --rig <name> \
  --rig-path <absolute path to the rig>
```

The flow:
1. Claims the bead (`bd update --claim`)
2. Runs the 16-step actor-critic pipeline: triage → baseline → plan →
   critique-plan ⟲ → build → lint+test (parallel) → regression-gate →
   review ⟲ → deploy-smoke → review-artifacts → verification ⟲ →
   ralph ⟲ → docs → demo → learn
3. Each role gets its own Claude session UUID (persisted in
   `metadata.json` so the role resumes on re-run)
4. Each turn spawns a tmux session `po-<issue>-<role>` that you can
   `tmux attach -t …` to watch live
5. Verdicts flow via file artifacts (`$RIG/.planning/software-dev-full/<issue>/verdicts/<step>.json`),
   not LLM-reply parsing
6. On success: closes the bead (`bd close`) with the run ID in the
   close reason
7. On failure anywhere: flow run shows Failed in Prefect UI; bead
   stays in_progress; re-run resumes from the failing step using
   persisted session UUIDs (no re-planning if plan already approved)

Commits land on the current git branch of the rig-path (`main` →
new branch; anything else → current). Git is the rollback mechanism.

## Dispatching a beads epic (DAG fan-out)

```bash
po run epic \
  --epic-id <epic-id> \
  --rig <name> \
  --rig-path <path>
```

Beads children of the epic (by dot-suffix ID convention: `<epic>.1`,
`<epic>.2`, …, OR by `bd dep` graph walk — see direction footgun
below) are discovered, topo-sorted, and each fans out as a concurrent
sub-flow chosen by per-child `metadata.formula` stamping (or the
formula default if unstamped), with `wait_for=[futures of its bd
deps]`.

Flags: `--max-issues N` (cap fan-out), `--dry-run` (DAG exercise with
StubBackend, no real Claude calls).

### bd dep direction footgun (applies to every pack)

PO's `list_subgraph` walks `bd dep list <epic> --direction=up` to find
children, so **children must depend on the epic**:

```bash
bd dep add CHILD EPIC --type=parent-child   # ← correct for PO
```

Not `bd dep add EPIC CHILD` — that's the **beadsd** (legacy daemon)
direction and produces `discovered 0 node(s) under <epic>` when
dispatched via `po run epic`. Verify after wiring:

```bash
bd dep list <epic> --direction=up --type=parent-child  # should list children
```

## Filing + dispatching an epic — generic recipe

Use this when the user has already laid out the shape of the work and
asked you to file beads + dispatch. Pack-specific guidance (which
formula to stamp, what conventions apply) belongs in the pack's own
skill; this recipe is the orchestration mechanics.

1. **Restate the scope** in 2-3 sentences and confirm before filing.
   Catch ambiguity here, not after PO is running.
2. **Decide epic vs single bead**:
   - One self-contained piece of work → single bead via the pack's
     primary formula (`po run <formula> --issue-id …`).
   - Multi-piece work with parallelizable children → epic.
3. **For epics, decompose into children** based on edit-conflict surface:
   - Independent files / new files → parallelizable.
   - Same shared file (e.g. one role prompt, one config) → serialize.
4. **File via bd**:
   ```bash
   bd create --type=epic --priority=2 --title="..." --description="..."
   bd create --type=task --priority=2 --title="..." --description="..."  # per child
   ```
   Run child creates in parallel where possible.
5. **Wire deps in PO direction** (children depend on epic; serial
   children depend on their upstream — see footgun above):
   ```bash
   bd dep add CHILD EPIC --type=parent-child
   bd dep add LATER_CHILD EARLIER_CHILD                 # serialization
   ```
6. **Stamp `metadata.formula` on every child** — this is how PO routes
   each child to its flow when fanning out the epic. The *key* is
   generic to PO; the *valid values* are whatever formulas the dispatched
   pack registers (e.g. `software-dev-full`, `software-dev-fast`,
   `software-dev-edit` from software-dev-pack). Load the pack's skill
   to know which value fits the work.
   ```bash
   bd update <child-id> --set-metadata formula=<formula-name>
   ```
   Stamp other pack-specific metadata at the same time (e.g.
   `metadata.branch`, `metadata.merge_strategy` for software-dev-pack-wts).
7. **Verify ready/blocked structure**:
   ```bash
   bd dep list <epic> --direction=up --type=parent-child  # should list ALL children
   bd ready                                               # should show only wave-1
   ```
8. **Set epic in_progress and dispatch via Bash tool with
   `run_in_background: true`** (NOT `nohup … & disown` — that orphans
   the process and you get no completion signal):
   ```
   command:  bd update <epic> --status=in_progress && po run epic --epic-id <epic> --rig <name> --rig-path <path> 2>&1 | tee /tmp/po-<epic>.log
   run_in_background: true
   ```
   The harness tracks the background process and notifies you on
   completion. Capture the bash_id so you can pull progress with
   `BashOutput` if asked.
9. **Report back immediately**: epic id, child ids + any pack-specific
   metadata (formula, branch, etc.), wave structure, log path. Don't
   tail the log; you'll get the completion signal automatically.
10. **On completion notification**: summarize results (which children
    completed, which hit rate-limits or failures, link to log). Don't
    spam intermediate updates.

For single-bead dispatch (step 2 path), the same `run_in_background: true`
rule applies — never use `nohup & disown`.

## Running an ad-hoc scratch flow (`po run --from-file`)

For a one-off `@flow` you want to dispatch without packaging it as an
installed pack with a `po.formulas` entry point:

```bash
po run --from-file ./scratch.py [--name <flow-name>] [--key value ...]
```

PO imports the file under a stable synthetic module name, locates the
`@flow`-decorated callable (auto-detected when the file defines exactly
one; pass `--name` otherwise), and dispatches it through the same path
as a registered formula — same kwargs parsing (`--key value`,
`--key=value`, bool/int/float coercion), same `_autoconfigure_prefect_api()`,
same exit codes.

Notes:
- Path resolves against CWD. Errors loudly on missing / non-`.py` paths.
- Multiple `@flow`s in one file → `po run --from-file x.py --name <flow>`.
- Scratch flows aren't registered, so they don't appear in `po list`.
- `po logs / artifacts / watch` (issue-id keyed) won't apply unless your
  scratch flow itself takes `issue_id` and writes a run_dir.
- **Security**: `--from-file` runs arbitrary Python in-process. It's a
  local single-user dev tool; treat the file like any other script.

## Filing work vs dispatching work

These are two different steps:

| Step | Tool | When |
|---|---|---|
| File an issue | `bd create --title=... --type=... --priority=...` | Captures *what* should be done |
| Dispatch implementation | `po run software-dev-full --issue-id ...` | Starts autonomous implementation of a single issue |
| Dispatch an epic | `po run epic --epic-id ...` | Starts autonomous fan-out of epic children |

Filing is still `bd`. Dispatching has moved from `beadsd dispatch` to
`po run`. `bd human <id>` is still the way to flag a bead for human
decision — PO flows call `bd human` internally when a step needs
signoff.

## Polyrepo rigs

If the rig's directory (`--rig-path`) is a bd root but not itself a
git repo — e.g., it contains several nested git repositories — the
builder agent must `cd` into the nearest `.git` ancestor of each file
it edits before running `git add`/`commit`. The rig's CLAUDE.md
should enumerate the sub-repos. Example: `polymer-dev/` has bd at
root, commits land in `polymer-dev/polymer/`, `polymer-dev/cdr/`,
`polymer-dev/rocks-geo/`, etc., depending on which files were touched.

## Debugging a run

```bash
po logs <issue-id>                  # tail the freshest log in run_dir
po logs <issue-id> -f               # follow
po artifacts <issue-id>             # dump full forensic trail (triage, plan, critiques, verdicts)
po sessions <issue-id>              # per-role Claude UUIDs (useful to tmux-attach or claude --resume)
po watch <issue-id>                 # live stream of Prefect state transitions + run-dir file changes
po retry <issue-id>                 # archive run_dir + relaunch fresh
po status                           # all active / recent PO runs, grouped by issue_id
po wait <issue-id>...               # block until issue(s) reach `closed` (exit 0/1/2/3)
```

Prefect UI at `http://127.0.0.1:4200` shows the DAG + task state.
Run-dir artifacts are canonical; the UI is a visualization.

### `po wait` vs `po watch` — when to use which

Both observe in-flight runs, but they serve different purposes:

| Use case | Command | Why |
|---|---|---|
| **Agent waiting for a flow to finish** | `po wait <id>` | Blocks until terminal, exits 0/1/2/3 — designed for `run_in_background: true` so the harness wakes you on exit |
| **Human watching live progress** | `po watch <id>` | Streaming UI: Prefect state transitions + run-dir file mtimes, runs until you Ctrl-C |
| **Snapshot of what's currently running** | `po status` | Tabular, returns immediately |
| **Forensic trail after a run finishes** | `po artifacts <id>` | Concatenates triage / plan / critiques / verdicts / decision-log / lessons-learned |

**Agent recipe — dispatch + wait + inspect:**

```bash
# 1. Dispatch (foreground in-process; exits when flow does):
po run software-dev-full --issue-id <id> --rig <name> --rig-path <path>

# OR — if you launched it elsewhere (`po retry`, another shell, an epic
# child) and want to be notified when it closes:
po wait <id> --timeout 1800             # exits when bd closes the issue
                                        # exit 0 = success-shaped close
                                        # exit 1 = `failed:` / `cap-exhausted` / `rejected:` / `regression:` / `force-closed`
                                        # exit 2 = timeout
                                        # exit 3 = bd unavailable / no such id

# Multiple issues — wait for ALL (default):
po wait <id1> <id2> <id3>

# Or first-to-close wins:
po wait <id1> <id2> --any
```

`po wait` polls `bd show --json` every `--poll` seconds (default 30s).
Match the verb-first convention agents follow (`approved: ...`,
`no regression: ...`, `failed: ...`); the failure-prefix matcher is
strict so `no regression: 763 passed` correctly counts as success.

When dispatching from an agent harness, prefer launching `po run`
itself with `run_in_background: true` (the harness gets a notification
on exit). Use `po wait` when the launch and the wait happen in
different processes — e.g. checking on epic children, or waiting on a
flow another teammate started.

## Backend selection

- Default: `TmuxClaudeBackend` when `tmux` is on PATH (lurkable via
  `tmux attach -t po-<issue>-<role>`).
- Fallback: `ClaudeCliBackend` (subprocess pipes, no lurking) when
  tmux missing.
- Force: `PO_BACKEND=cli`, `PO_BACKEND=tmux`, `PO_BACKEND=stub`
  (stub = no real Claude calls, for DAG tests).

Issue IDs with dots (`polymer-dev-abc.1`) are sanitized to underscores
in session names because tmux uses `.` as a pane separator.

## Concurrency caveats

- **One-writer beads backends** (embedded-dolt) serialize every
  `bd update` and will stall parallel epics. If the rig's `.beads`
  metadata has `dolt_mode: embedded`, migrate to `server` before
  running parallel epics against it.
- **Claude subscription rate limit**: running many PO flows alongside
  other concurrent Claude processes (beadsd, manual Claude Code
  sessions) can exhaust the concurrent-Opus allowance. Symptom:
  workers hang at triage or build for an hour+. Reduce parallelism
  or retry during quieter windows.
- **Prefect concurrency**: no practical cap — Prefect's work-pool +
  concurrency-limits (`prefect work-pool create po --concurrency-limit
  4`, `prefect concurrency-limit create critic 2`) handle this if
  you set them; otherwise it's unbounded by Prefect, bounded by
  Claude.

## What `po run` is for

PO is **orchestration on top of Claude Code**: deterministic-ish
workflows that run unattended, survive rate-limit pauses (resumable via
session UUIDs), can be lurked in tmux, and produce auditable artifacts
(decision-log, run-dir, lessons-learned). The **pack** you dispatch
decides what discipline gets enforced — formula selection is a
pack-specific concern, not a PO concern. Load the relevant pack skill
(e.g. `software-dev-pack`) for formula choice and pack-specific
conventions.

## When NOT to use `po run`

The deciding question: **does the work belong unattended in the
background, with verifiable artifacts, or in your conversation under
direct review?** PO is for the former. For the latter, use a subagent
or just do it.

- **Trivial / one-file edits**: just do them inline. The pipeline
  ceremony is overhead for a typo fix.
- **Read-only research** (locate files, audit conventions, summarize
  state): use the `Explore` or `general-purpose` subagent. PO has no
  read-only mode and will create planning/build/test artifacts you
  don't need.
- **Bounded single-step edits** (known files, known transformation, no
  ambiguity): subagent + parent review is faster than PO ceremony.
- **Pure prompt / markdown authorship through a code-shaped pack**
  (filling N `.md` or prompt-template bodies from a design doc): the
  pack's lint/test/critic gates are no-ops on `.md`; review caps eat
  wall-clock on LLM-on-LLM critique. Use parallel subagents instead.
- **Issues requiring heavy human product decisions mid-flow**: the
  actor-critic loop can't substitute for a human. Use `bd human` or
  just work the issue yourself.
- **Exploratory / research beads without clear acceptance criteria**:
  most pack verifiers expect ACs to check against. Without them they
  either rubber-stamp or reject nothing meaningfully.

### PO vs in-conversation subagent — the line

Choose **subagent** when the parent will consume the output and decide
the next step (research, prompt-authorship, bounded edits, parallel
investigation). No persistent decision-log / critic loop / run_dir.
~3-5 min per agent. Parallelize in a single message.

Choose **`po run`** when the work should run unattended (rate-limit
pause + resume, parallel fan-out, persistent artifacts), or benefits
from a pack's gating discipline (plan-critic, build-critic, verifier).
Lurkable in tmux; idempotent across pauses; epic-shaped with `po run
epic`.

**Anti-pattern**: dispatching a subagent for work that needs the pack's
verification gates — you'll end up re-verifying manually. Let PO and
the right pack's verifier role do their jobs.

<!-- po-skill-evals last-run: 2026-04-28T23:29:50Z n_pass=9/9 -->
