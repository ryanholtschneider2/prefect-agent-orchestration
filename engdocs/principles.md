# PO Engineering Principles

Durable design rules for `prefect-orchestration` core + first-party packs.
Add a principle here when a recurring judgment call has settled — not every
preference. Each principle should survive beyond one PR.

---

## 1. Thin CLI over Prefect — no duplication without value

> Add a `po` verb only when it composes something Prefect can't see (packs,
> entry points, rig-path, pack-declared deployment names) or collapses a
> multi-command ritual into one line. Otherwise defer to `prefect`.

**Why.** LLMs and humans already know the `prefect` CLI; every redundant
`po` wrapper doubles the API surface without adding capability. We want
people running nanocorps to spend their cognitive budget on formulas and
beads, not on learning a second CLI that re-exports the first.

**What passes the test.**

- `po list`, `po show`, `po run` — Prefect has no formula registry; these
  resolve the `po.formulas` entry-point group.
- `po deploy` / `po deploy --apply` — Prefect's deploy flow is YAML-first;
  we ship deployments as Python `register()` callables discovered via
  the `po.deployments` entry-point group.
- `po run <formula> --time 2h --args …` — resolves the pack's manual
  deployment name by convention so callers don't grep `po deploy` output.

**What fails the test.**

- `po worker`, `po server` — pure passthroughs to `prefect worker start`,
  `prefect server start`. Zero PO-specific logic.
- `po cancel`, `po ls-runs` — covered by `prefect flow-run ls` / `cancel`
  with no value we'd add.
- Anything that just re-exports a `prefect` subcommand under a `po` name.

**How to apply.** When you're about to add a `po <verb>`:

1. Write down the exact `prefect` command(s) it would replace.
2. Identify what PO knows that Prefect doesn't (entry points, rig-path,
   pack conventions, per-role concurrency tags, …).
3. If step 2 is empty, don't add the verb. Tell users the `prefect`
   command and move on.

**Exception — Python-only capability becomes a CLI verb.** Principle 1
says "don't duplicate Prefect's CLI." It does **not** say "don't add CLI
for things Prefect exposes only through Python." If a capability lives in
Prefect's Python API with no shell equivalent, wrapping it in a `po` verb
is a net add, not a duplication — the value is the shell invocation
itself. Default to exposing things through the CLI; treat "you have to
write Python for this" as friction to remove. Applies especially to:
running flows, triggering deployments, parameter-parsing conventions.

---

## 2. CLI first, Python second

Every capability should be reachable from the shell before we call it
shipped. `po run ...`, `po deploy ...`, `po list`, `po show` — not
`python -c 'from po_formulas import X; X(...)'`. Python APIs stay
available (flows, backends, telemetry) but are the fallback for
integration, not the primary UX.

**Why.**

- Operators drive nanocorps from terminals, `tmux`, CI, cron lines, and
  Slack slash-commands. None of those run Python.
- LLM coding agents compose shell commands an order of magnitude more
  fluently than they compose Python snippets — every Python-required
  step becomes a place where an agent writes a stray `import`, shadows
  an installed package, or picks the wrong venv.
- Shell commands are greppable in history and reproducible by paste;
  Python snippets are not.

**How to apply.**

1. When adding a feature, design the `po` invocation first. Write the
   README example line. Only then decide what Python surface supports it.
2. If a power user needs the Python API for deeper integration, it's
   fine — but the README's "how to use" section shows the shell form.
3. When you find yourself writing "here's a quick Python snippet…" in
   docs or a reply, ask whether that snippet should be a `po` verb
   instead. Usually the answer is yes.

---

## 3. PO owns the full pack lifecycle

Users and agents should only know **two** CLIs: `po` and `prefect`.
They should not have to learn `uv`, `pip`, `hatchling`, or
`[project.entry-points]`. Pack install, update, uninstall, and
inventory are PO's job.

**Why.**

- Every extra tool a user has to know is a place the abstraction
  leaks. `uv tool install --force --editable /path --with-editable
  /other/path` is Python-package-manager knowledge pretending to be
  PO knowledge.
- Packs are a PO concept (they register `po.formulas`,
  `po.deployments`, `po.commands`, `po.doctor_checks`, …). Only PO
  knows which installed Python distributions qualify as packs. The
  package manager sees opaque entry-points; `po` sees semantics.
- This is also how every mature platform CLI behaves: `kubectl
  plugin install`, `gh extension install`, `helm repo add`,
  `cargo install`. The package manager is plumbing.

**What PO is responsible for.**

- `po install <pack>` — from PyPI, git, or a local editable path
- `po install --editable <path>` — dev workflow for pack authors
- `po update <pack>` / `po update` (all) — re-install so changed
  entry-point metadata takes effect (entry-point groups are written
  at install time, not on code reload — a real footgun today)
- `po uninstall <pack>`
- `po packs` — inventory: version, source, what each contributes
  (formulas, deployments, commands, checks, integrations)

Under the hood PO can shell out to `uv tool …`. The user never
knows.

**How to apply.**

- If you're about to write docs that say "run `uv tool install
  --with-editable …`," stop. That's pack lifecycle; add or use a
  `po` verb instead.
- `[project.scripts]` in `pyproject.toml` is fine as an
  implementation detail (pack authors' Python tooling), but user-
  facing ops belong in the `po.commands` entry-point group so `po
  <command>` dispatches.

---

## 4. Two dispatch verbs, semantically distinct

PO distinguishes **orchestrated work** from **ad-hoc utility ops**:

- **`po run <formula>`** — runs a Prefect-orchestrated formula.
  Stateful, claims beads, produces a `$RUN_DIR`, composes
  actor/critic roles.
- **`po <command>`** — runs a pack-shipped utility callable or
  shell. One-shot, no Prefect overhead, no bead claim, no run dir.
  Examples: `po check-budget`, `po tail-logs-eks`, `po summarize-
  open-releases`.

Both are discoverable via `po list` (one column distinguishing
`formula` vs `command`), and introspectable via `po show <name>`.

**Why two verbs and not one.** The semantic categories are real: a
formula is an orchestrated workflow with observability + retries +
dep graph; a command is a script. Forcing both through `po run`
would either overload `run` (confusing) or hide the Prefect
semantics that make formulas different. Forcing both through `po
<name>` would silently upgrade every command into a Prefect flow
run, which is overkill and slow.

**Principle against duplication still holds**: there is exactly
**one** way to invoke each kind. Formulas are always `po run X`.
Commands are always `po X`. Not both for either.

**When packs register something, they pick one group.** An op that
does orchestrated, multi-step, LLM-bearing work → `po.formulas`.
An op that checks state, reports, or performs a one-shot action →
`po.commands`.

---

## Prompt authoring convention

Prompts are **plain markdown** with `{{var}}` substitution. No
Jinja2, no `{% include %}`, no fragment auto-compose. If two
prompts share rubric, duplicate it; duplication stays grep-able and
predictable. Agents (roles) live as folders:

```
<pack>/po_formulas/agents/
  triager/
    prompt.md
  builder/
    prompt.md
  critic/
    prompt.md
  ...
```

Optional future: `agents/<role>/config.toml` for model choice /
option defaults, when that need appears. Keep the default simple
until it doesn't work.

---
