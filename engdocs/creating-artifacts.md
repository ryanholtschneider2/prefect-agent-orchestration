# Creating PO artifacts — `po new pack|formula|skill|agent`

`po new` scaffolds a new PO-ecosystem artifact in the standard shape so you
don't hand-roll it. It's a `po.commands` utility op (pure transport — it emits
files from templates), not a flow: no Prefect overhead, no bead claim, no run
dir (principles §4).

```bash
po new pack    <name> [--path DIR] [--force]
po new formula <name> --pack <pack-root> [--force]
po new skill   <name> --pack <pack-root> [--force]
po new agent   <name> --pack <pack-root> [--force]
```

- `name` is lower-kebab (`my-thing`) — it becomes the dist name / entry-point
  key / directory.
- `--pack` is the filesystem path to an existing pack root (the dir holding the
  pack's `pyproject.toml`). Required for `formula`/`skill`/`agent`.
- `--path` is the parent dir for a new pack (default: cwd).
- `--force` overwrites existing files.

A worked example of all four lives at
[`examples/scaffold/po-hello/`](../examples/scaffold/po-hello/) — a pack with a
formula, a skill, and an agent scaffolded into it.

## When to use each

| Artifact | Use when you need… | Emits |
|---|---|---|
| **pack** | a new installable unit (formulas / commands / skills / drivers) | `pyproject.toml` (all `po.*` EP groups), `<module>/{__init__,commands}.py`, `README.md`, `overlay/CLAUDE-<name>.md` |
| **formula** | a new `@flow` pipeline dispatched via `po run` | `<module>/<name>_formula.py` (`(issue_id, rig, rig_path, *, parent_bead=None, dry_run=False)` + a verdict write) and a `po.formulas` EP |
| **skill** | a Claude-Code skill an agent loads, with a regression eval suite | `skills/<name>/SKILL.md` + `evals/{cases,rubrics}.yaml` |
| **agent** | a standing operating agent — prompt + trigger + **evals** | `<module>/agents/<name>/prompt.md`, `<module>/<name>_agent.py` (cron/event `@flow` running the agent via `AgentSession`) + a `po.formulas` EP, and an aeval suite under `evals/<name>/` |

## Standard shapes

### pack

A minimal installable pack. The sample `<name>-ping` command makes the pack
prove itself in `po list` the moment it's installed:

```bash
po new pack acme-tools
po packs install --editable acme-tools
po list                       # shows `acme-tools-ping`
po acme-tools-ping            # -> "acme-tools: pong"
```

The emitted `pyproject.toml` carries the hatchling build config, ships
`skills/` + `overlay/` in the wheel, and leaves the other `po.*` EP groups as
commented placeholders for `po new formula` / future additions to fill in.

### formula

```bash
po new formula nightly-rollup --pack acme-tools
po packs update                              # refresh EP metadata
po run nightly-rollup --issue-id <id> --rig <r> --rig-path <p>
```

The stub follows the signature convention and writes a verdict file
(`$RUN_DIR/verdicts/<step>.json`) as the orchestrator-readable pass/fail
artifact. On dolt rigs verdicts can instead live as bd metadata — see
[`verdict-channel-backends.md`](verdict-channel-backends.md). Replace the body
with the real pipeline (or compose `agent_step` for an agent-driven step).

### skill

```bash
po new skill billing --pack acme-tools
po run skill-evals --pack acme-tools --skill billing --dry-run   # CI-safe smoke
```

`SKILL.md` carries the frontmatter (`name` + `description`). The `evals/`
sibling follows the `skill-evals` schema ([`skill-evals.md`](skill-evals.md)):
`cases.yaml` (cases with tiers + evaluators) and `rubrics.yaml` (LLMJudge
criteria, `judge_model: claude-code`).

### agent

```bash
po new agent triage-bot --pack acme-tools
```

This is the one that closes the "every new agent ships with evals" loop. It
emits the full operating-agent shape:

- `agents/<name>/prompt.md` — the charter (what the agent owns, the bar for
  good) + the trigger (cron / bd event / mail).
- `<module>/<name>_agent.py` — a `@flow` that renders the prompt and runs one
  turn via `AgentSession` on the selected backend; register a deployment for it
  (`po run <name>-agent --at …` or a `po.deployments` `register()`).
- `evals/<name>/` — an aeval suite (`cases.yaml` + `rubrics.yaml` + `README.md`)
  with `judge_model: claude-code` (OAuth, not an API key) and a note to drive
  the agent-under-test on the tmux backend. Seed cases from real transcripts and
  every human escalation, per the agent-evals-best-practices conventions.

## Conventions honored

- **CLI-first, utility-op-not-flow** — `po new` is a direct `po.commands`
  callable (principles §2/§4), registered by core's own pyproject.
- **Pack convention** — emitted packs match
  [`pack-convention.md`](pack-convention.md): EP-group discovery, `overlay/` +
  `skills/` wheel inclusion, `evals/` sibling next to `SKILL.md`.
- **Entry-point insertion is text-surgical** — `formula`/`agent` insert their EP
  under the existing `[project.entry-points."po.formulas"]` header (or append a
  fresh section), preserving the rest of the file. A TOML round-trip would risk
  dropping comments and reordering EP blocks, which has bitten this repo before.
  Duplicate keys are refused.
