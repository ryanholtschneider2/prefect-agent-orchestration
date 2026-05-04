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
`<epic>.2`, …) are discovered, topo-sorted by their `bd dep` graph,
and each fans out as a concurrent `software-dev-full` sub-flow with
`wait_for=[futures of its bd deps]`.

Flags: `--max-issues N` (cap fan-out), `--dry-run` (DAG exercise with
StubBackend, no real Claude calls).

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
```

Prefect UI at `http://127.0.0.1:4200` shows the DAG + task state.
Run-dir artifacts are canonical; the UI is a visualization.

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

## When NOT to use `po run`

- **Trivial / one-file edits**: just do them inline. The 16-step
  pipeline is overhead for a typo fix.
- **Issues requiring heavy human product decisions mid-flow**: the
  actor-critic loop can't substitute for a human. Use `bd human` or
  just work the issue yourself.
- **Exploratory / research beads without clear acceptance criteria**:
  PO's verifier expects ACs to check against. Without them the
  verifier either rubber-stamps or rejects nothing meaningfully.

<!-- po-skill-evals last-run: 2026-04-28T23:29:50Z n_pass=9/9 -->
