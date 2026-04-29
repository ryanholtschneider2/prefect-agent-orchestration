# Plan — prefect-orchestration-etl

Migrate `po-formulas-retro/po_formulas_retro/flows.py` (384 LOC) from
the legacy direct-`AgentSession` + manual `_render` + manual
`read_verdict` + bespoke `_select_backend` pattern to the
`agent_step` + plain-Python composition pattern adopted by
`minimal_task` (`software-dev/po-formulas/po_formulas/minimal_task.py`,
~140 LOC). Preserve the `synthesize.json` verdict shape, the
`_apply_edit` polymorphism, the cross-repo edit gate, and all four
existing tests' observable behavior.

## Affected files

Under `/home/ryan-24/Desktop/Code/personal/nanocorps/po-formulas-retro/`:

- `po_formulas_retro/flows.py` — primary refactor target. Drop
  `_select_backend`, `_render`. Replace the `synthesize` task body
  with one `agent_step(...)` call. Add `_ensure_seed_bead` private
  helper. Add seed-bead lifecycle (create at top of flow, close at
  bottom).
- `po_formulas_retro/agents/synthesizer/prompt.md` — rewrite to a
  short identity prompt mirroring `software-dev/po-formulas/po_formulas/agents/triager/prompt.md`
  (`{{role_step_bead_id}}` + `{{role_step_close_block}}`).
- `po_formulas_retro/agents/synthesizer/task.md` — **new file**.
  Receives the bulk of the current `prompt.md` body (the actual job
  spec: allowed target files, output contract, inputs blob). agent_step
  stamps this onto the iter bead description; the agent reads it via
  `bd show <role_step_bead_id>`.
- `tests/test_flow.py` — rewire `_patch_backend` to patch
  `prefect_orchestration.agent_step._build_session` instead of a
  module-local `_select_backend` (which won't exist anymore). Add a
  `fake_bd` fixture (mirror of `tests/test_agent_step.py::fake_bd`)
  so agent_step's bead-stamping + convergence ladder don't shell out
  to a real `bd`.
- `tests/conftest.py` — minor: keep the `fake_target_repo`,
  `fake_rig_with_runs`, `stub_synthesis_payload` fixtures; possibly
  add a `seed_id` fixture if multiple tests need the same value.

Unchanged (verify only):

- `po_formulas_retro/analysis.py` (helpers stay; called at the same
  boundary).
- `po_formulas_retro/git_ops.py` (called from `apply_changes_and_commit`,
  unchanged).
- `po_formulas_retro/deployments.py` (re-imports
  `update_prompts_from_lessons`; flow object name + entry point
  preserved).

## Approach

### 1. Seed-bead lifecycle (new)

`agent_step` is built around the concept of a "seed bead" — a real
bd bead the agent operates against. The retro flow today has no
seed bead (it's a scheduled flow, not a bead-driven workflow). To
adopt agent_step we mint one per retro run:

```python
seed_id = f"retro-{_slug(target_pack)}-{timestamp}"  # e.g. "retro-po-formulas-software-dev-20260429T091500Z"
_ensure_seed_bead(
    seed_id,
    title=f"retro: {target_pack} @ {timestamp}",
    description=f"Auto-created by update_prompts_from_lessons for {target_pack} (since={since}).",
    rig_path=rig_path_p,
)
# ... agent_step / apply_changes / record_singles / write_summary ...
close_issue(
    seed_id,
    notes=f"complete: {len(recurring)} recurring, {len(singles_recorded)} singles, sha={commit.get('commit_sha') or 'none'}",
    rig_path=rig_path_p,
)
```

`_ensure_seed_bead` shells `bd create --id=<seed_id> --title=... --description=... --type=task -p 4`
with `cwd=rig_path`, treating "already exists" stderr as success
(idempotent on retry, mirroring `create_child_bead`'s pattern). When
`bd` isn't on PATH, it no-ops — agent_step itself degrades gracefully
in that case, so the flow remains testable without a real `bd`.

`_slug` strips non-`[A-Za-z0-9_-]` characters from the pack name so
the seed_id is bd-id-shaped.

**Why a real bead, not a synthetic id:** `create_child_bead` requires
the parent bead to exist (`bd create` rejects `--deps parent-child:<missing-id>`).
agent_step calls `create_child_bead(seed_id, "<seed>.synthesize.iter1", ...)`
when `iter_n=1`, so the seed has to be a real bead. Trying `iter_n=None`
to operate directly on the seed would also need a real bead for
`bd update --description` to succeed.

**Cost:** weekly cron => ~52 beads/year per target pack. Marked with
`-p 4` (low priority) and a stable `retro-` id prefix so they're
easy to filter / archive in bulk.

**Build-step verification (mandatory before shipping):** confirm
the target rig accepts `bd create --id=retro-…` (some bd
configurations enforce a fixed auto-id prefix per rig). 30-second
smoke from the target rig:

```bash
bd create --id=test-retro-id-smoke --title=x --type=task -p 4 --description=x
bd close test-retro-id-smoke --reason "smoke test"
```

If bd rejects the custom id, fall back inside `_ensure_seed_bead`:
let bd auto-assign (`bd create --title=... --description=... --type=task -p 4`),
parse the assigned id from stdout (`bd create` prints `Created
<id>: <title>`), and return that as the seed id from
`_ensure_seed_bead`. The flow's call site stays the same; only the
helper body changes (~10 LOC). Flag this during build if the smoke
fails.

### 2. Replace the `synthesize` task body with `agent_step`

The current `synthesize` task does six things: derive `verdict_path`,
build `AgentSession` with bespoke backend selection, render the
synthesizer prompt, prompt the session, read the verdict, filter +
return. The new shape is:

```python
@task(name="synthesize", tags=["synthesizer"])
def synthesize(
    seed_id: str,
    target_pack: str,
    target_repo_root: str,
    runs: list[dict[str, Any]],
    rig_path: str,
    dry_run: bool,
) -> dict[str, Any]:
    logger = get_run_logger()
    if not runs:
        logger.info("no runs collected; producing empty synthesis")
        return {"recurring": [], "single_occurrence": []}

    # Dry-run short-circuit: skip the agent turn entirely. Mirrors
    # apply_changes_and_commit's dry_run path. Avoids the StubBackend-
    # writes-no-verdict-file failure mode (StubBackend is what
    # agent_step would pick under dry_run=True, and it has no way to
    # produce a synthesize.json the flow could read back).
    if dry_run:
        logger.info("dry_run=True: skipping synthesizer turn")
        return {"recurring": [], "single_occurrence": []}

    agent_step_run_dir = (
        Path(rig_path).expanduser().resolve()
        / ".planning" / "agent-step" / seed_id
    )
    verdict_path = verdicts_dir(agent_step_run_dir) / "synthesize.json"

    agent_step(
        agent_dir=_AGENTS_DIR / "synthesizer",
        task=_AGENTS_DIR / "synthesizer" / "task.md",
        seed_id=seed_id,
        rig_path=rig_path,
        step="synthesize",
        iter_n=1,
        ctx={
            "target_pack": target_pack,
            "target_repo_root": target_repo_root,
            "n_runs": len(runs),
            "run_dir": str(agent_step_run_dir),
            "verdict_path": str(verdict_path),
            "lessons_blob": _format_lessons_blob(runs),
        },
        verdict_keywords=("synthesized", "no-recurring"),
    )

    payload = read_verdict(agent_step_run_dir, "synthesize")
    recurring_in = payload.get("recurring") or []
    singles_in = payload.get("single_occurrence") or []
    recurring = analysis.filter_recurring(
        [r for r in recurring_in if isinstance(r, dict)]
    )
    singles = [s for s in singles_in if isinstance(s, dict) and s.get("note")]
    logger.info(
        f"synthesized: {len(recurring)} recurring (after filter), "
        f"{len(singles)} single-occurrence"
    )
    return {"recurring": recurring, "single_occurrence": singles}
```

What dissolves vs persists:

| Old | New |
|---|---|
| `_select_backend()` | gone — agent_step picks via `select_default_backend()` / `dry_run` |
| `AgentSession(role=..., repo_path=..., backend=..., skip_mail_inject=True)` | gone — agent_step constructs the session; mail injection is opt-in (callers don't wire `mail_fetcher`/`mail_marker` so it's never on by default) |
| `_render("synthesizer", ...)` + `sess.prompt(rendered)` | gone — agent_step renders both `prompt.md` (identity) and `task.md` (job spec) and prompts |
| `read_verdict(run_dir_p, "synthesize")` | **kept** — agent_step doesn't read verdict files (it parses bead close-reasons); synthesize.json is a side-channel the agent writes per the task spec, and the flow consumes it |
| `analysis.filter_recurring(...)` | **kept** — defense-in-depth at the same boundary |
| `[s for s in singles_in if ... s.get("note")]` filter | **kept** — same shape |

### 3. Run-dir layout (two-dir intentional)

agent_step hardcodes its run_dir to `<rig>/.planning/agent-step/<seed_id>/`
(see `agent_step._build_session`). The retro flow's historical operator
artifact is `<rig>/.planning/update-prompts-from-lessons/<timestamp>/retro-<ts>.md`.

I'll keep **both**, with distinct purposes:

- `<rig>/.planning/agent-step/<seed_id>/` — owned by agent_step:
  - `verdicts/synthesize.json`
  - per-role session UUIDs (RoleSessionStore artifacts)
- `<rig>/.planning/update-prompts-from-lessons/<timestamp>/retro-<ts>.md`
  — operator-readable summary written by `write_summary`. Path is
  unchanged from current behavior, preserving any operator scripts
  that scan this dir.

This is a small wart but the alternative (consolidating under
`agent-step/`) breaks operator muscle memory and the pack's README
example. The plan flags this as a deliberate trade-off; if reviewers
prefer consolidation, swap in a one-line change to `flow_run_dir`.

### 4. Drop `_select_backend` (with one deliberate behavior change)

Delete the function entirely and its three imports (`ClaudeCliBackend`,
`StubBackend`, `TmuxClaudeBackend`, `shutil`). agent_step's
`select_default_backend()` (in `prefect_orchestration/backend_select.py`)
honors the same `PO_BACKEND=cli|tmux|stub` overrides AND raises
when `PO_BACKEND=tmux` is set without `tmux` on PATH — same as the
current `_select_backend()`.

**Behavior divergence on the auto/unset path** (deliberate, not
silent): the current `_select_backend()` chooses `TmuxClaudeBackend`
whenever `shutil.which("tmux")` is truthy.
`select_default_backend()` (lines 77-80 of `backend_select.py`)
adds an extra TTY gate — it picks `TmuxClaudeBackend` only when
**both** tmux is on PATH **and** `sys.stdout.isatty()` is True;
otherwise falls back to `ClaudeCliBackend`.

Net effect: under cron / Prefect worker (non-TTY stdout), retro
**switches from `TmuxClaudeBackend` to `ClaudeCliBackend`** in the
auto path. This is an improvement — tmux without a controlling
terminal is fragile (it works in practice today only because the
backend's session-create path tolerates non-TTY) — but it IS an
observable change for an operator who runs `po run update-prompts-from-lessons`
from an interactive shell with `tmux` installed: previously they'd
get a tmux session they could `tmux attach` to; now they only get
that when stdout is a TTY (which it is during interactive `po run`
invocations, so the practical impact is "scheduled cron runs no
longer create orphan tmux sessions").

Recommend: ship the change as-is; add a one-line note to the
pack's README under "Run" explaining `PO_BACKEND=tmux` to opt back
in. If reviewers prefer strict parity, pass an explicit
`backend=ClaudeCliBackend if not (shutil.which("tmux")) else TmuxClaudeBackend`
factory through the agent_step `backend=` kwarg — but this loses
the `PO_BACKEND` env override, which is a regression in its own
right. The TTY-gate divergence is the right call.

### 5. Drop `_render`

Replaced by `agent_step`'s internal `render_template` call on the
agent identity prompt and `_render_task` on the task spec. Both
consume the `ctx={...}` dict so all the existing template variables
(`target_pack`, `target_repo_root`, `n_runs`, `run_dir`,
`verdict_path`, `lessons_blob`) flow through unchanged.

### 6. Split `synthesizer/prompt.md` (and create new `task.md`)

**Create new file** at `po_formulas_retro/agents/synthesizer/task.md`
and **rewrite** `po_formulas_retro/agents/synthesizer/prompt.md`.

Current `prompt.md` (60 LOC) blends identity ("You are the
synthesizer") with job spec (allowed target files, output contract,
inputs blob). agent_step expects this split:

- `prompt.md` — small, stable identity (~20 LOC, mirrors
  `triager/prompt.md`):
  ```
  You are the **synthesizer** for the retro formula on pack `{{target_pack}}`.

  Your task spec lives in your role-step bead description. Read it first:

  ```bash
  bd show {{role_step_bead_id}}
  ```

  ... [boilerplate about the bead being canonical] ...

  {{role_step_close_block}}
  ```

- `task.md` — the bulk of the existing `prompt.md`: the "Your job"
  list, "Allowed target files", "Output contract", "Inputs"
  (`{{lessons_blob}}` rendered into the task description and stamped
  on the bead via `bd update --description`). The agent reads this
  via `bd show {{role_step_bead_id}}` as instructed by `prompt.md`.

  Append a close-the-bead instruction at the end so the agent uses
  one of the verdict keywords:
  ```
  ## Closing the bead

  After writing the verdict file, close this bead with one of:
  - `bd close {{role_step_bead_id}} --reason "synthesized: <theme-count> recurring themes"`
  - `bd close {{role_step_bead_id}} --reason "no-recurring: too few matches in sample"`
  ```

### 7. Preserved helpers

- `_apply_edit(target_path, edit)` — stays verbatim. Triage risk #2
  flagged this as load-bearing; the polymorphism (`isinstance(edit, str)`
  vs `dict.get("append")`) is **outside** agent_step's responsibility.
- `_format_lessons_blob(runs)` — stays verbatim.
- `_bd_remember(text)` — stays verbatim. Used by `record_singles`.
- `_utc_timestamp()` — stays verbatim.
- All `@task`s except `synthesize` keep their bodies:
  `locate_target_pack`, `collect_lessons`, `apply_changes_and_commit`,
  `record_singles`, `write_summary`.

### 8. Flow signature

Public signature unchanged:

```python
@flow(name=_FORMULA_NAME)
def update_prompts_from_lessons(
    target_pack: str,
    since: str = "7d",
    rig_path: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
```

No test-only kwargs added. Tests patch `agent_step._build_session`
to inject a fake session (matches `tests/test_agent_step.py`'s
established pattern).

### 9. Flow body (rough shape)

```python
rig = Path(rig_path or Path.cwd()).resolve()
timestamp = _utc_timestamp()
flow_run_dir = rig / ".planning" / _RUN_DIR_FORMULA / timestamp
flow_run_dir.mkdir(parents=True, exist_ok=True)

located = locate_target_pack(target_pack)
if "refused" in located:
    return {"refused": located["refused"], "run_dir": str(flow_run_dir)}

repo_root = located["repo_root"]
formulas = located["formulas"]
runs = collect_lessons(str(rig), formulas, since)

# NEW: mint seed bead (idempotent on retry).
seed_id = f"retro-{_slug(target_pack)}-{timestamp}"
_ensure_seed_bead(
    seed_id,
    title=f"retro: {target_pack} @ {timestamp}",
    description=f"Auto-created by update_prompts_from_lessons for {target_pack} (since={since}).",
    rig_path=rig,
)

synth = synthesize(
    seed_id=seed_id,
    target_pack=target_pack,
    target_repo_root=repo_root,
    runs=runs,
    rig_path=str(rig),
    dry_run=dry_run,
)

commit = apply_changes_and_commit(
    repo_root=repo_root,
    target_pack=target_pack,
    recurring=synth["recurring"],
    n_runs=len(runs),
    since=since,
    timestamp=timestamp,
    dry_run=dry_run,
)
singles_recorded = record_singles(synth["single_occurrence"], target_pack, dry_run)
summary_path = write_summary(
    run_dir=str(flow_run_dir),
    target_pack=target_pack,
    since=since,
    n_runs=len(runs),
    recurring=synth["recurring"],
    singles_recorded=singles_recorded,
    commit=commit,
    timestamp=timestamp,
)

# NEW: close seed bead with a one-line summary.
close_issue(
    seed_id,
    notes=(
        f"complete: {len(synth['recurring'])} recurring, "
        f"{len(singles_recorded)} singles, "
        f"sha={commit.get('commit_sha') or 'none'}"
    ),
    rig_path=rig,
)

return {
    "summary_path": summary_path,
    "commit": commit,
    "n_runs": len(runs),
    "recurring": synth["recurring"],
    "singles_recorded": singles_recorded,
    "run_dir": str(flow_run_dir),
    "seed_id": seed_id,  # NEW: callers (e.g. po watch) can resolve the agent_step run dir from this.
}
```

## Acceptance criteria

The bead description does not list explicit ACs (the migration is the
goal). Derived from the parent (`prefect-orchestration-etl`) bead +
triage risk list:

1. `flows.py` no longer imports `AgentSession`, `ClaudeCliBackend`,
   `StubBackend`, `TmuxClaudeBackend`, or `render_template`. It
   imports `agent_step` from `prefect_orchestration.agent_step` and
   `close_issue` from `prefect_orchestration.beads_meta`.
2. `_select_backend` and `_render` are deleted.
3. The synthesizer turn is dispatched via exactly one
   `agent_step(...)` call with `agent_dir=_AGENTS_DIR / "synthesizer"`,
   `task=_AGENTS_DIR / "synthesizer" / "task.md"`, `step="synthesize"`,
   `iter_n=1`, and a `dry_run=` kwarg threaded through from the flow.
4. The verdict file at `verdicts/synthesize.json` has the unchanged
   shape (`{"recurring": [...], "single_occurrence": [...]}`) with
   each `recurring[i]` having the same keys as today (`theme`,
   `evidence`, `target_file`, `edit`).
5. `_apply_edit` (str → full rewrite, `{"append": str}` → append-or-create)
   is preserved verbatim.
6. `analysis.filter_recurring` and `analysis.is_allowed_target_path`
   are still called at the same boundary
   (`apply_changes_and_commit`); the cross-repo gate
   (`is_allowed_target_path` + `target.relative_to(repo.resolve())`)
   is unchanged.
7. `record_singles` still routes single-occurrence lessons to
   `bd remember "[<target_pack>] <note>"`.
8. `dry_run=True` skips commits, skips `bd remember`, and still
   writes the summary artifact.
9. The `update_prompts_from_lessons` flow object is still importable
   from `po_formulas_retro.flows`; `deployments.register()` resolves
   without changes.
10. All four existing tests in `tests/test_flow.py` pass after
    rewiring (see Test plan below). New behavior (seed-bead creation
    / close) is also covered.
11. Existing `tests/test_analysis.py` and `tests/test_deployments.py`
    pass unchanged.

## Verification strategy

- **AC #1, #2**: `grep -E "AgentSession|render_template|_select_backend|_render\b" po_formulas_retro/flows.py` returns nothing. `grep "from prefect_orchestration.agent_step import agent_step" po_formulas_retro/flows.py` returns one line.
- **AC #3**: `grep -c "agent_step(" po_formulas_retro/flows.py` returns 1; the call is inside the `synthesize` `@task`.
- **AC #4**: `tests/test_flow.py::test_three_matching_runs_produce_prompt_edit` reads back the verdict shape via `result["recurring"][0]["target_file"]` etc.; the test passes unchanged in observable behavior.
- **AC #5**: `tests/test_flow.py::test_three_matching_runs_produce_prompt_edit` already asserts the appended text is in the edited file (`"Run \`bd prime\` after a session reset" in edited.read_text()`); covers `{"append": str}`. Add a new test case (or extend the canned payload) that uses the str-rewrite branch and asserts `target.read_text() == "<full new contents>"`.
- **AC #6**: existing `tests/test_analysis.py::test_is_allowed_target_path*` cases (assumed to exist; verify) plus a new test in `test_flow.py` that injects a recurring item with `target_file="../../../etc/passwd"` and asserts it's dropped from `commit["edited_files"]`.
- **AC #7**: existing `_patch_bd_remember` capture path; `bd_calls == [f"[{target_pack}] {note}"]`.
- **AC #8**: existing `test_dry_run_skips_commit_and_bd_remember` asserts `pre_sha == post_sha`, `commit["commit_sha"] is None`, `bd_calls == []`, summary exists.
- **AC #9**: `python -c "from po_formulas_retro.deployments import register; print(register()[0].name)"` prints `retro-weekly`.
- **AC #10**: `uv run python -m pytest tests/test_flow.py -v` — all four old tests pass; new seed-bead tests pass.
- **AC #11**: `uv run python -m pytest tests/ -v` — all green.

## Test plan

**Layer**: unit only (under `po-formulas-retro/tests/`). The retro
pack has no `tests/e2e/` and no UI. Per
`prefect-orchestration/.po-env`, e2e is skipped by default in the
parent rig too — but this isn't the parent rig, this is `po-formulas-retro`,
which has its own `tests/` (unit) layout.

### Test changes

1. **Add `fake_bd` fixture** to `tests/conftest.py` (or `test_flow.py`):
   - Mirrors `prefect-orchestration/tests/test_agent_step.py::fake_bd`.
   - Patches `prefect_orchestration.agent_step._bd_show`,
     `create_child_bead`, `close_issue`, `_bd_available`,
     and `subprocess.run` (for `bd update --description`).
   - In-memory dict represents bd state; the seed bead is pre-seeded
     with status="open"; the iter bead is created on
     `create_child_bead` call.
   - Also patches `flows.close_issue` and (new) `flows._ensure_seed_bead`
     so the flow's own bd shellouts are inert.

2. **Rewrite `_patch_backend` helper** in `test_flow.py`:
   - Replace `monkeypatch.setattr(flows, "_select_backend", lambda: backend)`
     with `monkeypatch.setattr(agent_step_mod, "_build_session", lambda **_kw: fake_session)`.
   - **Verdict-path discovery (concrete mechanism — replaces the
     iter1 prompt-regex approach which no longer works).** In the
     new flow, the verdict path is *not* in the agent's identity
     prompt; it lives in `task.md`, which `agent_step._stamp_description`
     ships to bd via `bd update <bead_id> --description <task.md_rendered>`.
     The `fake_bd` fixture's `subprocess.run` patch intercepts that
     specific shellout shape: when `argv[:3] == ["bd", "update", <id>]`
     and `--description` is in `argv`, it captures the description
     value into `fake_bd[bead_id]["description"]`. The fake session's
     `prompt(text, **kw)` then reads `fake_bd[<iter_bead_id>]["description"]`
     and regex-extracts `(/[^\s\`]+/verdicts/[\w\-.]+\.json)` from
     **that** to find `verdict_path`, writes the canned payload,
     and marks the iter bead `status="closed"` with reason
     `"synthesized: <theme-count> recurring themes"`. This keeps
     agent_step's convergence ladder on the happy "agent closed
     bead" path.
   - The iter bead id is deterministic: `<seed_id>.synthesize.iter1`.
     The fake session computes it from the seed_id passed to
     `agent_step` via the captured `_build_session` call args
     (or, simpler: the fake session iterates `fake_bd` looking for
     the most recently created bead matching `*.synthesize.iter1`).

3. **Existing tests** (rewire, do not change semantics):
   - `test_three_matching_runs_produce_prompt_edit` — still asserts
     verdict shape, edited file contents, branch creation, summary
     path, bd_remember call.
   - `test_main_branch_creates_retro_branch` — still asserts retro
     branch shape.
   - `test_feature_branch_commits_in_place` — still asserts in-place
     commit.
   - `test_refuses_on_non_editable_target` — `flows.locate_target_pack`
     returns `{"refused": ...}` BEFORE agent_step is reached, so this
     test keeps its "backend should not be selected" assertion (just
     swap "backend" → "agent_step's `_build_session`" in the explode
     callback).
   - `test_dry_run_skips_commit_and_bd_remember` — adjust to the
     new short-circuit: the fake session must NOT be invoked when
     `dry_run=True` (the synthesize task short-circuits before
     calling agent_step). Replace the existing
     `_patch_backend(monkeypatch, _CannedBackend(stub_synthesis_payload))`
     with a fake whose `prompt()` raises `AssertionError("dry-run
     should not invoke the agent")`. Then assert: no commit / no
     bd_remember / summary present / `result["recurring"] == []`
     / `result["singles_recorded"] == []`. (Today's test passes
     because the agent runs and the apply path short-circuits;
     under the new flow the agent itself is short-circuited, which
     is a deliberate behavior tighten — see Risk #8.)

4. **New tests**:
   - `test_seed_bead_created_and_closed` — assert
     `_ensure_seed_bead` is called once with `seed_id` matching
     `retro-<slug>-<ts>`, and `close_issue(seed_id, ...)` is called
     once at the end with a notes string containing the recurring
     count.
   - `test_str_edit_branch` — extend `stub_synthesis_payload` (or
     parametrize) with one item whose `edit` is a string (full
     rewrite) and assert `target.read_text() == <string>`.
   - `test_disallowed_target_filtered` — inject a recurring item
     with `target_file="../../../etc/passwd"` (or any path failing
     `is_allowed_target_path`) and assert it's NOT in
     `commit["edited_files"]`.

5. **`tests/test_analysis.py` and `tests/test_deployments.py`**:
   no changes expected. Run them to confirm.

### Test commands

```bash
cd /home/ryan-24/Desktop/Code/personal/nanocorps/po-formulas-retro
uv run python -m pytest tests/ -v
# Optional smoke (no real Claude calls; uses StubBackend via PO_BACKEND=stub):
PO_BACKEND=stub uv run python -m pytest tests/test_flow.py -v
```

## Risks

1. **Seed-bead creation pollutes bd.** Every retro run mints a
   `retro-<slug>-<timestamp>` bead. At weekly cron cadence per pack
   that's manageable (~52/year/pack). Mitigation: low priority
   (`-p 4`) and a stable `retro-` id prefix make these trivial to
   filter or mass-archive. Not a blocker.

2. **Two run dirs** (`<rig>/.planning/agent-step/<seed>/` for
   verdicts/sessions vs `<rig>/.planning/update-prompts-from-lessons/<ts>/`
   for the operator summary). Slightly worse discoverability;
   chosen to preserve the README's documented summary path. If
   reviewers prefer consolidation, the change is one-line in
   `update_prompts_from_lessons` (point `flow_run_dir` at the
   agent_step run dir). The plan keeps both intentionally.

3. **Verdict file location migration.** Today: `<rig>/.planning/update-prompts-from-lessons/<ts>/verdicts/synthesize.json`.
   New: `<rig>/.planning/agent-step/<seed_id>/verdicts/synthesize.json`.
   This is a behavior change for any operator script that reads the
   raw verdict directly. The summary file path (the visible artifact)
   does NOT change. README example doesn't reference the verdict
   path, so this is internal-only.

4. **`render_template` overlay differences — verified clean.**
   `ls po_formulas_retro/agents/synthesizer/` shows only `prompt.md`,
   so agent_step's optional `identity.toml` / `memory/MEMORY.md`
   overlays are inert. No surprise merge.

5. **`{{verdict_path}}` substitution path.** The task.md template
   includes `{{verdict_path}}` so the agent knows where to write.
   agent_step's `_safe_substitute` is permissive (unknown vars stay
   literal). We pass `verdict_path` in `ctx`, so substitution
   succeeds. If a future refactor renames the ctx key, the agent
   silently writes nowhere — flag in the task.md prompt that the
   path is critical.

6. **Test backend stubbing surface change.** Tests now patch
   `agent_step._build_session` (a private helper). If
   `prefect_orchestration` ever refactors `agent_step` internals, the
   tests break. Mitigation: this matches `tests/test_agent_step.py`'s
   own pattern; the core repo's tests are the upstream contract for
   the patching surface, so they'll fail in lock-step if the surface
   changes.

7. **Cross-repo edit gate** (triage risk #8). The refactor must NOT
   loosen `is_allowed_target_path` or the `target.relative_to(repo.resolve())`
   check — both still live in `apply_changes_and_commit`, unchanged.
   New test `test_disallowed_target_filtered` makes this explicit.

8. **`StubBackend` in dry-run path — solved by short-circuit.** When
   `dry_run=True`, agent_step would force `StubBackend`, which by
   definition does NOT write the verdict file → `read_verdict` would
   raise `FileNotFoundError`. The `synthesize` task's `if dry_run:
   return {"recurring": [], "single_occurrence": []}` short-circuit
   (see §2 / §"Flow body") fires BEFORE `agent_step` is called, so
   the StubBackend path is never exercised in production. Behavior
   match with current code: today's `dry_run=True` still calls the
   agent and writes a verdict file (then `apply_changes_and_commit`
   short-circuits later). Under the new flow, dry_run produces an
   empty `recurring`/`singles` payload directly. Net difference:
   dry-runs are now **faster** (no agent turn) and **cheaper** (no
   token spend), but the test's existing `_CannedBackend`-driven
   dry-run check needs to be updated to match the new short-circuit
   behavior — see Test plan §3 below.

9. **`PO_FORMULA_MODE` interaction.** `software_dev_full` reads
   `PO_FORMULA_MODE=legacy|graph` to switch implementations. Retro
   does NOT need this gate — there's no graph-mode for retro and no
   long-term legacy body to keep around. Don't add a mode env var
   here; keep the flow body single.

10. **Dependencies.** `agent_step` is exported from
    `prefect_orchestration.agent_step`, available since the agent_step
    convention landed (4ja series, 7vs.5). No version bump or new
    dep needed in `po-formulas-retro/pyproject.toml`. Verify by
    grepping the existing dep declaration.
