# Plan — prefect-orchestration-dgr

## Issue

Deprecate `po-formulas-prompt`'s `prompt_run` formula by replacing it
with a thin stub that emits `DeprecationWarning` and delegates to the
core `prefect_orchestration.agent_step:agent_step` (the underlying
function the `agent-step` flow wraps). Migration for callers should be
one-line / one-invocation.

This is a **cross-repo self-dev** issue:

| Path | Role |
|---|---|
| `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration` | rig (where `bd` and the test venv live; baseline runs from here) |
| `/home/ryan-24/Desktop/Code/personal/nanocorps/po-formulas-prompt` | **pack_path** — code edits and `git` commits land here |

## Affected files (under `pack_path`)

1. `/home/ryan-24/Desktop/Code/personal/nanocorps/po-formulas-prompt/po_formulas_prompt/__init__.py`
   — replace the ~280-line `prompt_run` body with a stub that:
   - Emits a per-call `DeprecationWarning` referencing core
     `agent-step` and the migration path.
   - Validates `rig_path` exists (raises `FileNotFoundError` — same
     shape as today).
   - Auto-creates a `po-prompt`-labeled bead with description = prompt
     (small `_bd_create` helper kept from the legacy file).
   - Stamps `po.agent=<role>`, `po.rig_path=<rig_path_p>`,
     `po.run_dir=<rig>/.planning/agent-step/<bd>` via one
     `bd update --set-metadata` call.
   - Calls `agent_step(agent_dir=discover_agent_dir(role),
     task=None, seed_id=<bd_id>, rig_path=<rig_path>, dry_run=...)`.
   - Returns a dict with all 7 legacy keys (some `None`) so existing
     callers don't `KeyError`.
2. **(new)** `/home/ryan-24/Desktop/Code/personal/nanocorps/po-formulas-prompt/po_formulas_prompt/agents/general/prompt.md`
   — minimal pass-through identity (~10 lines): "You are a general
   agent. Read your bead description (`bd show <bead>`) for the
   user's request. Do the work. When complete, close the bead with
   `bd close <bead> --reason 'complete: <one-line summary>'`."
   Resolved by `discover_agent_dir`'s pack-fallback. **Required:**
   without this file, every `prompt_run(role="general")` call (the
   default!) hits `LookupError("agent role 'general' not found")`
   inside `agent_step` — verified by code-reading
   `prefect_orchestration/formulas.py:54-103`.
3. `/home/ryan-24/Desktop/Code/personal/nanocorps/po-formulas-prompt/pyproject.toml`
   — bump `version` (`0.1.0` → `0.2.0`); update `description` to
   "[deprecated] thin stub delegating to core `agent-step` formula";
   leave the `po.formulas` entry-point name unchanged so
   `po run prompt …` keeps working. Add
   `[tool.hatch.build.targets.wheel.force-include]` mapping
   `"po_formulas_prompt/agents" = "po_formulas_prompt/agents"` so
   the new prompt file ships in a wheel install (mirrors what core
   does at `prefect-orchestration/pyproject.toml:55-57`).
4. `/home/ryan-24/Desktop/Code/personal/nanocorps/po-formulas-prompt/README.md`
   — prepend a deprecation banner; add a migration block:
   - Old: `po run prompt --prompt "/foo" --rig-path /path`
   - New (one-liner): `BD=$(bd create --title "/foo" --description
     "/foo" --set-metadata po.agent=general -q) && po run agent-step
     --issue-id "$BD" --rig <rig> --rig-path /path`
5. **(new)** `/home/ryan-24/Desktop/Code/personal/nanocorps/po-formulas-prompt/tests/test_deprecation.py`
   — pytest file (`po-formulas-prompt` currently has no `tests/` dir).
6. **(new)** `/home/ryan-24/Desktop/Code/personal/nanocorps/po-formulas-prompt/tests/__init__.py`
   — empty file so pytest collects the new test module without
   import-mode fallback gymnastics.

No edits in the rig (`prefect-orchestration/`). The rig's baseline
(762 passed / 1 skipped) must remain green.

## Approach

### Why a stub, not a delete

The bead spec says "stub + deprecation warning + one-line caller
migration." This is the standard 1-cycle deprecation: callers continue
to function under a `DeprecationWarning`, get one release window to
migrate, then a follow-up bead deletes the package. Hard-deleting now
without a stub would mean `po run prompt` errors out for any installed
caller. A live-caller audit (see Risks #4) shows zero external callers
today, but past planning docs reference the package and shipping a
stub is cheap insurance.

### Reconciling the four behavioral mismatches

The triage identified three behavioral differences. Code-reading
`prefect_orchestration/agent_step.py` surfaces a fourth (agent prompt
file requirement). The stub handles all four:

| Mismatch | Old (`prompt_run`) | New (`agent_step`) | Stub does |
|---|---|---|---|
| Bead title / metadata | `[po-prompt] <slug>` title; no `po.agent` metadata | reads `po.agent` metadata for role | Stub creates bead with old-style title **and** stamps `po.agent=<role>`. The stub also passes `agent=role` explicitly to the delegate so it does not depend on metadata read-back. |
| Artifact layout | `<rig>/.planning/prompt/<bd>/{prompt.md,reply.md,session_id.txt}` | `<rig>/.planning/agent-step/<bd>/` (managed by `agent_step`) | Stub no longer writes legacy `prompt.md`/`reply.md`. Bead description carries the prompt; reply lives via session UUID inside `agent_step`'s run_dir; `po artifacts <bd>` works because `po.run_dir` is stamped. The deprecation message points callers at `bd show <id>` or `po artifacts <id>`. |
| Backend selection | `cli` / `stub` / `tmux-stream` / `tmux` (interactive) | `cli` / `stub` / `tmux` (streaming-only) — see `backend_select.py:36-80` | Stub honors `PO_BACKEND` via `select_default_backend()` (called inside `agent_step`). `PO_BACKEND=tmux-stream` and `PO_BACKEND=tmux-interactive` are no longer recognized — `select_default_backend` falls through to auto-pick (= streaming `TmuxClaudeBackend`). Deprecation message names this regression. |
| **Agent identity prompt** (4th mismatch) | `prompt_run` sent the user prompt verbatim through `AgentSession` with no role-prompt header (legacy `__init__.py:213`) | `agent_step` renders `<agent_dir>/prompt.md` as the agent identity (`agent_step.py:443-455`) and uses the bead description as the task | Stub ships `po_formulas_prompt/agents/general/prompt.md` (minimal pass-through). `discover_agent_dir`'s pack-fallback (`formulas.py:76-97`) walks `po.formulas` entry-point modules → resolves the new file. Other roles need either a registered `po.agents` entry or an `agents/<role>/prompt.md` somewhere in an installed pack. The deprecation message tells callers passing `--role <unknown>` what to do. |

### Why "one-line caller migration"

Today (deprecated form, still works):

```bash
po run prompt --prompt "/get-data Bolivia geophysics" --rig-path /path
```

Migrated (post-stub-deletion form, callers run this directly):

```bash
BD=$(bd create --title "/get-data Bolivia geophysics" \
                --description "/get-data Bolivia geophysics" \
                --set-metadata po.agent=general -q)
po run agent-step --issue-id "$BD" --rig polymer-dev --rig-path /path
```

Two commands but mechanically equivalent: precondition (create bead) +
dispatch. Reasonable to call the second line "the migration" since
the first is shell precondition. During the deprecation window the
stub keeps the single-command form working.

### Stub skeleton (the core of the change)

Roughly:

```python
import warnings
from pathlib import Path
from typing import Any

from prefect import flow

from prefect_orchestration.agent_step import agent_step
from prefect_orchestration.formulas import discover_agent_dir


@flow(name="prompt", flow_run_name="{label}-{role}", log_prints=True)
def prompt_run(
    prompt: str,
    rig_path: str,
    role: str = "general",
    model: str = "opus",
    label: str | None = None,
    dry_run: bool = False,
    create_bead: bool = True,
    close_on_success: bool = True,
) -> dict[str, Any]:
    warnings.warn(
        "po-formulas-prompt.prompt_run is deprecated; use core "
        "`agent-step` formula via `bd create … --set-metadata "
        "po.agent=<role>` + `po run agent-step --issue-id <bd> "
        "--rig <r> --rig-path <p>`. Stub keeps the old call shape "
        "alive for one release. Lost affordances: `prompt.md` / "
        "`reply.md` on-disk artifacts, `tmux-interactive` backend "
        "(use `PO_BACKEND=tmux` for streaming attach). See "
        "po-formulas-prompt/README.md.",
        DeprecationWarning,
        stacklevel=2,
    )
    rig_path_p = Path(rig_path).expanduser().resolve()
    if not rig_path_p.exists():
        raise FileNotFoundError(f"rig_path does not exist: {rig_path_p}")

    label = label or _slug_from_prompt(prompt)
    bd_id: str | None = None
    if create_bead and not dry_run and _bd_available(rig_path_p):
        bd_id = _bd_create(rig_path_p, label, prompt, role, model)
        if bd_id:
            run_dir = rig_path_p / ".planning" / "agent-step" / bd_id
            _bd_set_metadata(
                rig_path_p, bd_id,
                **{
                    "po.agent": role,
                    "po.rig_path": str(rig_path_p),
                    "po.run_dir": str(run_dir),
                },
            )

    seed_id = bd_id or f"prompt-{label}"
    agent_dir = discover_agent_dir(role)  # raises LookupError on miss
    agent_step(
        agent_dir=agent_dir,
        task=None,                # bead description IS the task spec
        seed_id=seed_id,
        rig_path=str(rig_path_p),
        dry_run=dry_run,
    )

    return {
        "label": label,
        "bd_id": bd_id,
        "role": role,
        "run_dir": str(rig_path_p / ".planning" / "agent-step" / seed_id),
        "reply_path": None,   # no longer written
        "session_id": None,   # owned by RoleSessionStore now
        "tmux_session": None, # owned by select_default_backend now
    }
```

`_slug_from_prompt`, `_bd_available`, `_bd_create`,
`_bd_set_metadata` from the existing file are kept (small, self-
contained, well-tested by usage). Everything else
(`_pick_backend_factory`, `_make_backend`, `AgentSession` / backend
imports, `_bd_claim`, `_bd_close`, `create_markdown_artifact`, the
`flow_run` tag-update logic) is deleted because `agent_step` owns
those concerns now.

### Sub-flow vs direct function call

The stub calls **`agent_step()` (the module-level function)**, not
`agent_step_flow()` (the `@flow` wrapper). Both take the same kwargs
and the function-form is what `agent_step_flow` itself wraps. Direct
function call avoids a nested Prefect flow run per dispatch (one
flow run per `po run prompt` invocation instead of two), which keeps
the Prefect UI clean and matches the principle that the stub is
purely a shim. Trivial role-resolution logic (`agent or
_read_meta(...)`) from `agent_step_flow` (`formulas.py:152-157`) is
not needed inline because the stub already has `role` in scope from
its own arg.

### Refusing `tmux-interactive` cleanly

Per the triage: outright dropping `tmux-interactive` is a regression
unless we confirm no caller uses it. The audit (Risk #4) finds zero
external callers, so this is acceptable as a deprecation cycle. The
stub's deprecation message explicitly names `tmux-interactive` as a
dropped affordance.

## Acceptance criteria (verbatim from issue)

> Action: deprecate po-formulas-prompt, add a stub flow that emits a
> deprecation warning + delegates to agent_step. Migration is one-line
> in caller code.

Concretely:

- **AC1:** Calling `prompt_run(...)` (or `po run prompt …`) emits a
  `DeprecationWarning` at runtime.
- **AC2:** The stub successfully delegates to
  `prefect_orchestration.agent_step:agent_step` for the actual agent
  dispatch — no duplicate AgentSession / backend / artifact code
  remains in `po_formulas_prompt`.
- **AC3:** The README documents the migration path (one-line / one-
  invocation form using `bd create … --set-metadata po.agent=<role>` +
  `po run agent-step …`).
- **AC4:** `po list` (with `po-formulas-prompt` installed) still
  surfaces the `prompt` formula (entry-point name preserved).

## Verification strategy

| AC | How verified |
|---|---|
| AC1 | Pytest using `pytest.warns(DeprecationWarning)` around an in-process call to `prompt_run.fn(...)` (using `.fn` to bypass the Prefect flow runner) with `dry_run=True`. Asserts the warning message contains "deprecated" and "agent-step". |
| AC2 | Static check: `grep -E "AgentSession\|TmuxInteractiveClaudeBackend\|create_markdown_artifact" po_formulas_prompt/__init__.py` returns nothing. Plus a unit test that monkeypatches `po_formulas_prompt.agent_step` (the imported callable) with a recording stub and asserts it was called once with kwargs `agent_dir=<resolved Path>, task=None, seed_id=<bd_id>, rig_path=<str>, dry_run=False`. |
| AC3 | `grep -F "agent-step" README.md` returns the migration block; first paragraph contains the word "deprecated". Manual eyeball of README. |
| AC4 | `po packs update && po list` (manual smoke on workstation) shows `prompt` row with the new package version. Not pytest-gated. |

## Test plan

`po-formulas-prompt` today has **no `tests/` directory**. Per
CLAUDE.md `tests/` is split unit/e2e/playwright; this stub is small
enough that one **unit** file suffices.

- **`tests/test_deprecation.py`** (unit, 4 tests):
  1. `prompt_run.fn(...)` emits `DeprecationWarning` whose message
     mentions both "deprecated" and "agent-step". Use
     `pytest.warns(DeprecationWarning)`. Pass `dry_run=True` and a
     `tmp_path` rig with `.beads/` initialised (or skip-bd path —
     see test 3).
  2. Delegation: monkeypatch `po_formulas_prompt.agent_step` with a
     recording stub (returning a `SimpleNamespace`); assert it was
     called exactly once with the expected kwargs (esp. `task=None`,
     `agent_dir` resolves to the bundled
     `po_formulas_prompt/agents/general/`).
  3. Bead-stamping (skipped when `bd` not on PATH; matches the
     soft-bd pattern at `prompt_formula.py:50-51`): with a
     `tmp_path` rig `bd init`'d, run the stub and assert the
     created bead has metadata keys `po.agent`, `po.rig_path`,
     **and** `po.run_dir` set to the expected values.
  4. Return shape: returned dict has all 7 legacy keys (`label`,
     `bd_id`, `role`, `run_dir`, `reply_path`, `session_id`,
     `tmux_session`) — assertion is `set(result.keys()) ==
     LEGACY_KEYS`. Values may be `None` for `reply_path`,
     `session_id`, `tmux_session`; `run_dir` and `bd_id` non-`None`.

  **Module-level `DeprecationWarning` on import is intentionally
  NOT tested** — Python caches modules in `sys.modules`, so a test
  using `pytest.warns(...)` around `import po_formulas_prompt` is
  flaky depending on test ordering (the first test to import it
  consumes the warning; later imports are no-ops). Per-call warning
  (test 1) is the affordance callers actually see at runtime.

- **No e2e** — the rig's `.po-env` sets `PO_SKIP_E2E=1`; the rig's
  e2e suite tests core `po`/`bd` roundtrips and doesn't import
  `po_formulas_prompt`. Adding e2e here would only re-exercise core's
  `agent_step` indirectly; not worth the wall-clock.

- **No playwright** — no UI surface.

- **Rig baseline guard:** the rig's suite (762 passed / 1 skipped)
  must remain green. The pack is not in the rig's dependency graph,
  so this should be a non-event, but the actor-critic loop will
  re-run the baseline as the regression gate.

## Risks

1. **Caller-facing regression: dropped `tmux-interactive` UX.**
   `select_default_backend` returns `TmuxClaudeBackend` (streaming),
   not the interactive-attach variant. Anyone relying on "open
   `po run prompt` and immediately see Claude typing live in my
   current tty" loses that. Mitigation: explicit warning in the
   deprecation message; the audit (Risk #4) finds zero external
   callers, so impact is bounded to forensic / future use.

2. **Caller-facing regression: dropped on-disk markdown artifacts.**
   Old: `<rig>/.planning/prompt/<bd>/{prompt.md,reply.md,session_id.txt}`.
   New: bead description + `agent_step` run_dir at
   `<rig>/.planning/agent-step/<bd>/`. Anyone scraping the old paths
   from a downstream script breaks. The audit shows no such scrapers
   exist today. Mitigation: deprecation message names this; suggests
   `po artifacts <bd>` (works thanks to the `po.run_dir` stamp) or
   `bd show <bd> --json` as the replacement read.

3. **Entry-point name collision with core.** Both core
   `prefect-orchestration` (`prompt = …prompt_formula:prompt_run`)
   and `po-formulas-prompt`
   (`prompt = po_formulas_prompt:prompt_run`) register the
   `po.formulas` entry-point name `prompt`. `importlib.metadata` does
   NOT error on duplicates — it returns both, and `po list` /
   `po run` resolution picks one (typically install order). Note
   that core's `prompt_formula.py` is also redundant with
   `agent_step` and is itself a candidate for the same stub
   treatment. **Out-of-scope but flagged for follow-up:** the
   builder will file a new bead (`bd create --title "Stub core
   prompt_formula.py same way as po-formulas-prompt-dgr" …`) so the
   loop closes. Don't expand this bead's scope to cover core.

4. **Live-caller audit (resolved at planning time).** During
   planning, `grep -rln "po_formulas_prompt\|po run prompt"
   ~/Desktop/Code --include={*.py,*.sh,*.toml,*.md}` (excluding
   `.venv`, `.beads`, `.planning`, `__pycache__`, `.git`,
   `node_modules`) returned **zero** external hits. The only matches
   are inside `po-formulas-prompt/` itself (the package source +
   docs) and `prefect-orchestration/prompt_formula.py` (core's
   parallel redundant copy, see Risk #3). No live external caller
   exists — the stub is purely insurance.

5. **Hatchling `force-include` requirement for prompt file.** The
   new `po_formulas_prompt/agents/general/prompt.md` is a non-`.py`
   file. Hatchling's default wheel target is `.py`-centric, so the
   prompt file would be missing in a wheel install. Mitigation: add
   `[tool.hatch.build.targets.wheel.force-include]` mapping
   `"po_formulas_prompt/agents" = "po_formulas_prompt/agents"` to
   `pyproject.toml` (mirrors core's
   `prefect-orchestration/pyproject.toml:55-57`). Editable installs
   are unaffected (they read the source tree directly).

6. **No final-removal bead filed yet.** This bead only adds the
   stub; a future bead removes the package once the deprecation
   window closes. Out of scope here, but the builder should
   `bd create` a follow-up referencing
   `prefect-orchestration-dgr` so the cycle closes.

7. **Cross-repo commit landing.** The pack lives at
   `/home/ryan-24/Desktop/Code/personal/nanocorps/po-formulas-prompt/`
   and is its own git repo. The builder must `cd` into the pack repo
   before `git add` / `git commit` — `git -C
   /home/ryan-24/Desktop/Code/personal/nanocorps/po-formulas-prompt
   rev-parse --show-toplevel` should return the pack dir. Use scoped
   `git add <path>` (pyproject.toml, README.md,
   po_formulas_prompt/__init__.py, po_formulas_prompt/agents/,
   tests/), never `git add -A`. The rig's `.beads/` and `.planning/`
   artifacts stay in the rig repo and get committed separately as
   part of the PO loop's bead-state tracking.
