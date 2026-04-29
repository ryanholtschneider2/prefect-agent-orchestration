# Plan — prefect-orchestration-dgr

**Cross-repo self-dev:**

| Path | Role |
|---|---|
| `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration` | rig (where `bd` claim/close + run_dir + baseline + tests live) |
| `/home/ryan-24/Desktop/Code/personal/nanocorps/po-formulas-prompt` | **pack_path** — code edits + `git` commits land here |

## Issue Summary

`po-formulas-prompt`'s `prompt_run` formula is functionally redundant
with the core `prefect_orchestration.formulas:agent_step_flow` (which
wraps `prefect_orchestration.agent_step:agent_step`). Both auto-create
a bead for one Claude turn, dispatch one agent against it, and persist
the reply. The action is to replace `prompt_run`'s ~280-line body
with a thin stub that emits `DeprecationWarning` and delegates to
`agent_step`, so existing callers (zero today, by audit) continue to
function for one release cycle. A separate follow-up bead will fully
remove the package once the deprecation window closes; another
follow-up will give core's parallel `prompt_formula.py` the same stub
treatment.

## Research Summary

### Existing code patterns

- **`po-formulas-prompt/po_formulas_prompt/__init__.py`** — the
  current ~280-line `prompt_run`. Builds its own `AgentSession`,
  picks backend with `_pick_backend_factory` (`cli|stub|tmux-stream|
  tmux`-interactive), creates a bead labelled `po-prompt`, stamps
  `po.rig_path`/`po.run_dir`, writes `prompt.md`/`reply.md` to disk,
  emits a Prefect `create_markdown_artifact`, claims + closes the
  bead itself.
- **`prefect-orchestration/prefect_orchestration/agent_step.py`** —
  the modern primitive. `agent_step(agent_dir, task, seed_id, rig_
  path, ctx, iter_n, step, verdict_keywords, session_role, backend,
  dry_run) -> AgentStepResult`. Resolves target bead (seed or
  `<seed>.<step>.iter<N>`), reads bead description as task spec,
  renders `<agent_dir>/prompt.md` as agent identity, picks backend
  via `select_default_backend()`, runs the turn, parses verdict
  from the agent's `bd close` reason, defensively force-closes if
  the agent didn't close. Run-dir is **deterministic**:
  `rig_path / ".planning" / "agent-step" / seed_id`
  (`agent_step.py:412-414`).
- **`prefect-orchestration/prefect_orchestration/formulas.py`** —
  `agent_step_flow` is the `@flow` wrapper that resolves the role
  via `agent` arg or `po.agent` metadata, then calls `agent_step()`.
  `discover_agent_dir(role)` (lines 41-103) walks `po.agents`
  entry-points first, then falls back to scanning each `po.formulas`
  pack's module dir for `agents/<role>/prompt.md`. **The fallback
  is what makes shipping `po_formulas_prompt/agents/general/prompt.md`
  sufficient — no extra entry-point registration needed.**
- **`prefect-orchestration/prefect_orchestration/backend_select.py`**
  — `select_default_backend()` honors `PO_BACKEND=cli|tmux|stub` and
  auto-picks streaming `TmuxClaudeBackend` when tmux is on PATH and
  stdout is a TTY, else `ClaudeCliBackend`. **No interactive
  variant.** `tmux-stream` and `tmux-interactive` are no longer
  recognized — they fall through to the auto-pick branch.
- **`prefect-orchestration/pyproject.toml:55-57`** — pattern for
  shipping non-`.py` files in a wheel:
  `[tool.hatch.build.targets.wheel.force-include]` mapping
  `"prefect_orchestration/agents" = "prefect_orchestration/agents"`.
  Required because hatchling's default wheel target is `.py`-centric.

### engdocs review

- `engdocs/principles.md` §"Prompt authoring convention" — agents
  live as folders `<pack>/agents/<role>/prompt.md`, plain markdown,
  no Jinja, no fragment auto-compose. **The new `agents/general/
  prompt.md` follows this convention exactly.**
- `engdocs/pack-convention.md` — packs declare formulas via
  `po.formulas` entry-point; entry-point name `prompt` is preserved
  by the stub so `po run prompt` keeps working.
- `engdocs/principles.md` §"Don't add a `po` verb that just wraps a
  `prefect` subcommand" — **does NOT apply here.** This is a stub
  inside a pack, not a new core verb; the stub is a pack-internal
  shim during the deprecation window.
- No decision record opposes deprecating `po-formulas-prompt`.

### Live-caller audit (run during planning)

```
grep -rln "po_formulas_prompt\|po run prompt" ~/Desktop/Code \
  --include={*.py,*.sh,*.toml,*.md} \
  | grep -vE "/(\.venv|\.beads|\.planning|__pycache__|\.git|node_modules)/"
```

Result: **zero external callers**. Hits are limited to:
- `po-formulas-prompt/po_formulas_prompt/__init__.py` (the package
  itself)
- `po-formulas-prompt/README.md`, `pyproject.toml` (self-docs)
- `prefect-orchestration/prefect_orchestration/prompt_formula.py`
  (core's parallel redundant copy — out of scope; follow-up bead)
- `.planning/` artifacts and triage docs (transient)

No real-world consumer breaks if the stub's behaviour diverges from
the legacy. The stub is purely insurance against any caller revealed
later.

### Design decisions + trade-offs

1. **Stub vs hard delete.** Stub keeps the entry-point name `prompt`
   resolvable for one release cycle. Cheap insurance. (Issue
   explicitly asks for stub.)
2. **Call `agent_step()` (function) vs `agent_step_flow()` (`@flow`)**
   — call the **function**. The `@flow` wrapper's only added value
   is the `agent`-from-metadata fallback (lines 152-157), which the
   stub doesn't need because it has `role` in scope. Calling the
   `@flow` would create a nested Prefect flow run per dispatch, which
   pollutes the UI for no benefit.
3. **Default `role="general"` requires shipping
   `agents/general/prompt.md`** — without it, `agent_step` (via
   `discover_agent_dir`) raises `LookupError` on every default-role
   call. The pack-fallback resolution path (`formulas.py:76-97`)
   resolves files inside `po_formulas_prompt/agents/`, so adding the
   file there is sufficient.
4. **Drop `tmux-interactive` backend selection.** Audit shows zero
   external callers; deprecation message names the loss; the
   streaming `TmuxClaudeBackend` (still attachable via
   `tmux attach -t …`) is the closest analogue for the auto-pick
   path.
5. **Stamp `po.rig_path`/`po.run_dir` on the stub-created bead.**
   `agent_step` does NOT stamp these (verified — only
   `role_registry.build_registry`, `skill_evals`, and the legacy
   `prompt_formula` do). Without them, `po watch / artifacts /
   sessions / retry / logs` show "(missing)" for stub-created
   beads. Run_dir is deterministic, so the stub stamps it
   pre-emptively.
6. **Keep all 7 legacy return-dict keys.** Some become `None`
   (`reply_path`, `session_id`, `tmux_session`) because the stub
   doesn't own those concerns anymore; doing this preserves dict
   access shape so silent `KeyError` regressions don't bite a
   Python caller. Test 4 locks the key set.

## Success Criteria

### Acceptance criteria (verbatim from issue)

> Action: deprecate po-formulas-prompt, add a stub flow that emits a
> deprecation warning + delegates to agent_step. Migration is one-line
> in caller code.

Decomposed into testable conditions:

- **AC1:** Calling `prompt_run(...)` (or `po run prompt …`) emits a
  `DeprecationWarning` at runtime.
- **AC2:** The stub successfully delegates to
  `prefect_orchestration.agent_step:agent_step` for the actual agent
  dispatch — no duplicate `AgentSession` / backend / artifact code
  remains in `po_formulas_prompt`.
- **AC3:** The README documents the migration path (one-line / one-
  invocation form using `bd create … --set-metadata po.agent=<role>` +
  `po run agent-step …`).
- **AC4:** `po list` (with `po-formulas-prompt` installed) still
  surfaces the `prompt` formula (entry-point name preserved); the
  rig's pytest baseline (762 passed / 1 skipped) remains green.

### Demo / output shape

Successful demo: `PO_BACKEND=stub po run prompt --prompt "/foo"
--rig-path /tmp/rig --dry-run` emits a `DeprecationWarning` to
stderr, creates a `po-prompt`-labelled bead in the rig (when
`bd init`'d), dispatches `agent_step` against it (with stub
backend, no real Claude call), returns a dict with all 7 legacy
keys. `po artifacts <bd>` against the resulting bead resolves the
`agent-step/<bd>/` run_dir and shows the (stub-empty) artifacts.

## Files to Modify/Create

All paths are absolute under
`/home/ryan-24/Desktop/Code/personal/nanocorps/po-formulas-prompt/`.

| File | Action | Rationale |
|---|---|---|
| `po_formulas_prompt/__init__.py` | **rewrite** (~280 LOC → ~80 LOC) | Replace fat formula body with stub: validate + create bead + stamp metadata + delegate to `agent_step()`. Keep `_slug_from_prompt`, `_bd_available`, `_bd_create`, `_bd_set_metadata` helpers. Delete `_bd_claim`, `_bd_close`, `_pick_backend_factory`, `_make_backend`, all `AgentSession`/backend imports, `create_markdown_artifact` import, the `flow_run`-tag-update logic, the on-disk artifact writes. |
| `po_formulas_prompt/agents/general/prompt.md` | **new** | Required so `discover_agent_dir("general")` resolves via the pack-fallback (`formulas.py:76-97`). Without it, every default-role `prompt_run` call hits `LookupError` inside `agent_step`. ~10 lines: minimal pass-through identity instructing the agent to read its bead description and close on completion. |
| `pyproject.toml` | **edit** | Bump `version` 0.1.0 → 0.2.0; update `description` to mark stub status; **leave entry-point name `prompt` unchanged**; add `[tool.hatch.build.targets.wheel.force-include]` for `po_formulas_prompt/agents/`. |
| `README.md` | **edit** | Prepend deprecation banner; add a migration block showing the new `bd create … --set-metadata po.agent=general` + `po run agent-step …` shape. List dropped affordances (markdown artifacts, `tmux-interactive`). |
| `tests/__init__.py` | **new** | Empty file — pytest discovers `tests/test_*.py` without import-mode fallbacks. |
| `tests/test_deprecation.py` | **new** | Four-test unit file (see Test Plan). |

### Skeleton — `po_formulas_prompt/__init__.py`

```python
"""[deprecated] po formula: `prompt` — thin stub delegating to core
`agent_step`. Will be removed in a future release; migrate callers
to `agent-step` via `bd create … --set-metadata po.agent=<role>`.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import warnings
from pathlib import Path
from typing import Any

from prefect import flow

from prefect_orchestration.agent_step import agent_step
from prefect_orchestration.formulas import discover_agent_dir


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_BD_LABEL = "po-prompt"
_DEPRECATION_MESSAGE = (
    "po-formulas-prompt.prompt_run is deprecated; use core `agent-step` "
    "formula via `bd create … --set-metadata po.agent=<role>` + "
    "`po run agent-step --issue-id <bd> --rig <r> --rig-path <p>`. "
    "Stub keeps the old call shape alive for one release. Lost "
    "affordances: `prompt.md` / `reply.md` on-disk artifacts, "
    "`tmux-interactive` backend (use `PO_BACKEND=tmux` for streaming "
    "attach). See po-formulas-prompt/README.md."
)
_LEGACY_KEYS = {
    "label", "bd_id", "role", "run_dir",
    "reply_path", "session_id", "tmux_session",
}


def _slug_from_prompt(prompt: str, max_words: int = 6, max_len: int = 40) -> str:
    text = prompt.strip().lstrip("/")
    words = _SLUG_STRIP.sub(" ", text.lower()).split()[:max_words]
    base = "-".join(words)[:max_len].strip("-") or "prompt"
    return f"{base}-{hashlib.sha1(prompt.encode()).hexdigest()[:6]}"


def _bd_available(rig: Path) -> bool:
    return shutil.which("bd") is not None and (rig / ".beads").exists()


def _bd_create(rig: Path, slug: str, prompt: str, role: str, model: str) -> str | None:
    title = f"[po-prompt] {slug}"
    description = (
        f"Auto-created by `po run prompt` (deprecated stub).\n\n"
        f"**Role**: {role} · **Model**: {model}\n\n"
        f"## Prompt\n\n```\n{prompt}\n```\n"
    )
    proc = subprocess.run(
        ["bd", "create", "--title", title, "--description", description,
         "--type", "task", "--priority", "3", "--label", _BD_LABEL, "--json"],
        cwd=rig, capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        m = re.search(r"\b([a-z0-9-]+-[a-z0-9]+)\b", proc.stdout + proc.stderr)
        return m.group(1) if m else None
    return (data.get("id") or data.get("issue_id")) if isinstance(data, dict) else None


def _bd_set_metadata(rig: Path, bd_id: str, **kv: str) -> None:
    args = ["bd", "update", bd_id]
    for k, v in kv.items():
        args.extend(["--set-metadata", f"{k}={v}"])
    subprocess.run(args, cwd=rig, capture_output=True, check=False)


@flow(name="prompt", flow_run_name="{label}-{role}", log_prints=True)
def prompt_run(
    prompt: str,
    rig_path: str,
    role: str = "general",
    model: str = "opus",
    label: str | None = None,
    dry_run: bool = False,
    create_bead: bool = True,
    close_on_success: bool = True,  # accepted for back-compat; ignored
) -> dict[str, Any]:
    warnings.warn(_DEPRECATION_MESSAGE, DeprecationWarning, stacklevel=2)

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
    agent_dir = discover_agent_dir(role)
    agent_step(
        agent_dir=agent_dir,
        task=None,
        seed_id=seed_id,
        rig_path=str(rig_path_p),
        dry_run=dry_run,
    )

    return {
        "label": label,
        "bd_id": bd_id,
        "role": role,
        "run_dir": str(rig_path_p / ".planning" / "agent-step" / seed_id),
        "reply_path": None,
        "session_id": None,
        "tmux_session": None,
    }
```

### Skeleton — `po_formulas_prompt/agents/general/prompt.md`

```markdown
You are a general-purpose agent dispatched against a single beads
issue. Read your bead description for the user's request:

```bash
bd show {{seed_id}}
```

Do the requested work. Use whatever tools you have (Read, Edit,
Bash, etc.). When complete, close the bead:

```bash
bd close {{seed_id}} --reason "complete: <one-line summary>"
```

If you're blocked or need a human decision, leave the bead open and
flag it: `bd human {{seed_id}} --question="<one-line question>"`.

{{role_step_close_block}}
```

## Implementation Steps

1. **Set up tests/ directory in the pack.** `cd
   /home/ryan-24/Desktop/Code/personal/nanocorps/po-formulas-prompt`,
   `mkdir tests`, `touch tests/__init__.py`. **Checkpoint:** `ls
   tests/` shows `__init__.py`.
2. **Add the `general` agent prompt.** Create
   `po_formulas_prompt/agents/general/prompt.md` with the skeleton
   above. **Checkpoint:** `python -c "from
   prefect_orchestration.formulas import discover_agent_dir; import
   po_formulas_prompt; print(discover_agent_dir('general'))"` prints
   the path under `po_formulas_prompt/agents/general/`.
3. **Update `pyproject.toml`.** Bump version, update description,
   add `[tool.hatch.build.targets.wheel.force-include]`. **Checkpoint:**
   `cd /tmp && python -m build /home/ryan-24/.../po-formulas-prompt
   --wheel` (or skip — editable installs don't need this for local
   tests, only for downstream wheel consumption).
4. **Rewrite `po_formulas_prompt/__init__.py`.** Per skeleton above.
   **Checkpoint:** `grep -E "AgentSession|TmuxInteractiveClaudeBackend|create_markdown_artifact"
   po_formulas_prompt/__init__.py` returns nothing; the file is ≤100
   lines (down from ~280).
5. **Write `tests/test_deprecation.py`.** Four tests per Test Plan
   below. **Checkpoint:** `cd po-formulas-prompt && uv run python -m
   pytest tests/ -q` passes 4/4 (skipping bd-test if `bd` is not on
   PATH).
6. **Update `README.md`.** Deprecation banner + migration block.
   **Checkpoint:** `grep -F "agent-step" README.md` returns the
   migration block; first paragraph contains "deprecated".
7. **Re-run the rig baseline.** `cd
   /home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration
   && uv run python -m pytest tests/ --ignore=tests/e2e
   --ignore=tests/playwright --tb=short`. **Checkpoint:** matches
   baseline (762 passed / 1 skipped) — no regression.
8. **`po packs update` smoke (manual).** Refreshes entry-point
   metadata after the pyproject change. **Checkpoint:** `po list`
   shows `prompt` row.
9. **File the two follow-up beads.** (a) Full removal of
   `po-formulas-prompt` after the deprecation window;
   (b) same stub treatment for core `prompt_formula.py`. Both
   reference `prefect-orchestration-dgr` in their description.
10. **Commit in the pack repo.** `cd po-formulas-prompt && git add
    pyproject.toml README.md po_formulas_prompt/__init__.py
    po_formulas_prompt/agents/general/prompt.md tests/__init__.py
    tests/test_deprecation.py && git commit -m "[dgr] deprecate
    prompt_run: stub delegating to core agent_step"`. **Checkpoint:**
    `git -C po-formulas-prompt status` clean; `git -C
    prefect-orchestration status` only shows `.planning/` artifacts.

## Verification Strategy

| Criterion | Verification Method | Concrete Check |
|---|---|---|
| **AC1** — `DeprecationWarning` fires per call | unit test | `tests/test_deprecation.py::test_warning_on_call`: `with pytest.warns(DeprecationWarning, match="deprecated.*agent-step"): prompt_run.fn(prompt="x", rig_path=str(tmp_path), dry_run=True, create_bead=False)`. The `.fn` attribute bypasses Prefect's flow runner so we can call it directly. |
| **AC2** — delegates to `agent_step` | static + unit test | (a) `grep -E "AgentSession\|TmuxInteractiveClaudeBackend\|create_markdown_artifact" po_formulas_prompt/__init__.py` returns no matches. (b) `tests/test_deprecation.py::test_delegates_to_agent_step` monkeypatches `po_formulas_prompt.agent_step` with a recording stub, calls `prompt_run.fn(...)`, asserts the stub received `task=None`, `agent_dir.name == "general"`, `seed_id` non-empty, `rig_path` matches input. |
| **AC3** — README migration path | static check | `grep -F "agent-step" README.md` returns ≥ 1 hit inside a fenced code block; `head -20 README.md \| grep -i "deprecated"` returns ≥ 1 hit; manual eyeball that the migration block uses `bd create … --set-metadata po.agent=general`. |
| **AC4 part 1** — entry-point preserved | smoke | `po packs update && po list \| grep "^prompt "` returns the `prompt` row with the package's new version 0.2.0. (Manual on workstation; not pytest-gated because it requires `po packs update` shell-out.) |
| **AC4 part 2** — rig baseline green | regression test | `cd prefect-orchestration && uv run python -m pytest tests/ --ignore=tests/e2e --ignore=tests/playwright --tb=short \| tail -1` shows `762 passed, 1 skipped` (matches baseline). The actor-critic loop's `run_tests` task auto-runs this. |
| **B2 lock-in** — bead metadata stamped | unit test | `tests/test_deprecation.py::test_bd_metadata_stamped` (skipped when `bd` not on PATH): with a `tmp_path` rig + `bd init`, run `prompt_run.fn(...)`, then `bd show <bd> --json` returns metadata `{po.agent: "general", po.rig_path: <rig>, po.run_dir: <rig>/.planning/agent-step/<bd>}`. |
| **N6 lock-in** — return-dict shape | unit test | `tests/test_deprecation.py::test_return_dict_shape` with `dry_run=True`: `set(result.keys()) == {"label","bd_id","role","run_dir","reply_path","session_id","tmux_session"}`; `result["bd_id"] is None` (since `dry_run` skips bead creation); `result["run_dir"]` is non-`None`; `result["reply_path"] is result["session_id"] is result["tmux_session"] is None`. |

## Test Plan

`po-formulas-prompt` currently has **no `tests/` directory**. Per the
rig's CLAUDE.md `tests/` is split unit/e2e/playwright; this stub is
small enough that one **unit** file suffices.

### Test layer applicability

- **Unit (yes)** — one new file `tests/test_deprecation.py`, four
  tests. Mocking `bd` is fine here per the rig's testing convention.
  Test 3 (bead metadata) skips when `bd` isn't on PATH.
- **E2E (no)** — the rig's `.po-env` sets `PO_SKIP_E2E=1`. The rig's
  e2e suite tests core `po`/`bd` roundtrips and doesn't import
  `po_formulas_prompt`. Adding e2e here would only re-exercise core's
  `agent_step` indirectly; not worth the wall-clock.
- **Playwright (N/A)** — no UI surface.

### Specific tests to add

`po-formulas-prompt/tests/test_deprecation.py`:

1. `test_warning_on_call` — `pytest.warns(DeprecationWarning,
   match="deprecated.*agent-step")` around `prompt_run.fn(...)` with
   `dry_run=True`, `create_bead=False`, `tmp_path` rig.
2. `test_delegates_to_agent_step` — monkeypatch
   `po_formulas_prompt.agent_step` with a recording stub returning
   a `SimpleNamespace`. Assert exactly-one call, kwargs include
   `task=None` and `agent_dir.name == "general"`.
3. `test_bd_metadata_stamped` — `pytest.mark.skipif(shutil.which("bd")
   is None or shutil.which("dolt") is None, reason="bd not on PATH")`
   guards. With a `tmp_path` rig `bd init`'d (or copy from a fixture),
   run `prompt_run.fn(...)`, assert all three metadata keys
   (`po.agent`, `po.rig_path`, `po.run_dir`) are stamped.
4. `test_return_dict_shape` — `dry_run=True`, `create_bead=False`.
   Assert `set(result.keys()) == LEGACY_KEYS`; specific values per
   AC4-N6 lock-in row above.

**Module-level `DeprecationWarning` on import is intentionally NOT
tested.** Python caches modules in `sys.modules`, so a test using
`pytest.warns(...)` around `import po_formulas_prompt` is flaky
depending on test ordering (the first test to import it consumes
the warning; later imports are no-ops). Per-call warning (test 1)
is the affordance callers actually see at runtime.

### Rig baseline guard

The rig's suite (762 passed / 1 skipped — see baseline.txt) must
remain green. The pack is not in the rig's dependency graph, so
this should be a non-event, but the actor-critic loop will re-run
the baseline as the regression gate.

## Risks

1. **Caller-facing regression: dropped `tmux-interactive` UX.**
   `select_default_backend` returns `TmuxClaudeBackend` (streaming),
   not the interactive-attach variant. Anyone relying on "open
   `po run prompt` and immediately see Claude typing live in my
   current tty" loses that. Mitigation: explicit warning in the
   deprecation message; the audit (Risk #4) finds zero external
   callers, so impact is bounded. **No rollback needed.**

2. **Caller-facing regression: dropped on-disk markdown artifacts.**
   Old: `<rig>/.planning/prompt/<bd>/{prompt.md,reply.md,session_id.txt}`.
   New: bead description + `agent_step` run_dir at
   `<rig>/.planning/agent-step/<bd>/`. Anyone scraping the old paths
   from a downstream script breaks. Audit shows no such scrapers
   exist today. Mitigation: deprecation message names this; suggests
   `po artifacts <bd>` (works thanks to the `po.run_dir` stamp) or
   `bd show <bd> --json` as the replacement read. **No rollback
   needed.**

3. **Entry-point name collision with core.** Both core
   `prefect-orchestration` (`prompt = …prompt_formula:prompt_run`)
   and `po-formulas-prompt`
   (`prompt = po_formulas_prompt:prompt_run`) register the same
   `po.formulas` entry-point name. `importlib.metadata` does NOT
   error on duplicates — it returns both, and `po list` / `po run`
   resolution picks one (typically install order). Note that core's
   `prompt_formula.py` is also redundant with `agent_step` and is
   itself a candidate for the same stub treatment. **Out-of-scope
   but flagged for follow-up:** the builder will file a new bead
   so the loop closes (Implementation Step 9). Don't expand this
   bead's scope.

4. **Live-caller audit (resolved at planning time).** `grep -rln
   "po_formulas_prompt\|po run prompt" ~/Desktop/Code
   --include={*.py,*.sh,*.toml,*.md}` (excluding `.venv`, `.beads`,
   `.planning`, `__pycache__`, `.git`, `node_modules`) returned
   **zero** external hits. The only matches are inside
   `po-formulas-prompt/` itself + core's `prompt_formula.py` (Risk
   #3). No live external caller exists.

5. **Hatchling `force-include` requirement for prompt file.**
   `agents/general/prompt.md` is a non-`.py` file; without
   `[tool.hatch.build.targets.wheel.force-include]`, wheel installs
   miss the prompt file and `discover_agent_dir("general")` errors.
   Editable installs unaffected (read source tree directly).
   Mitigation: implementation step 3 adds the force-include block,
   matching core's pattern at
   `prefect-orchestration/pyproject.toml:55-57`.

6. **No final-removal bead filed yet.** This bead only adds the
   stub. Implementation Step 9 files the follow-up bead; if the
   builder skips that step, the deprecation never closes.
   Mitigation: step 9 is a hard checkpoint, not optional.

7. **Cross-repo commit landing.** `git add` / `git commit` must
   happen inside `po-formulas-prompt/` (a separate git repo from
   the rig). Implementation Step 10 enforces `cd po-formulas-prompt
   && git add <scoped paths>` and verifies `git -C po-formulas-prompt
   rev-parse --show-toplevel` returns the pack dir. The rig's
   `.beads/` and `.planning/` artifacts stay in the rig repo and
   commit separately as part of the PO loop's bead-state tracking.
   **Rollback:** `git -C po-formulas-prompt reset --hard HEAD~1` if
   a regression appears post-commit but pre-`git push` (note: this
   repo has no remote configured per the rig CLAUDE.md, so the only
   "publish" event is local).

8. **No rollback plan needed beyond standard git.** No migrations,
   no schema changes, no API contracts visible to humans
   (entry-point name preserved). If post-merge a bug emerges, the
   single-commit rollback is `git revert <sha>` in the pack repo.
