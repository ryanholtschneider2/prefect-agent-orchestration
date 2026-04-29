# Plan — prefect-orchestration-etl

## Issue Summary

Migrate `po-formulas-retro/po_formulas_retro/flows.py` (384 LOC) from
the legacy direct-`AgentSession` + manual `_render` + manual
`read_verdict` + bespoke `_select_backend` pattern to the
`agent_step` + plain-Python composition pattern that
`software-dev/po-formulas/po_formulas/minimal_task.py` (~140 LOC)
exemplifies. This is a **refactor / convergence task, not a behavior
change** — the synthesizer's job, the `synthesize.json` verdict
shape (`recurring[]` / `single_occurrence[]`), the `_apply_edit`
polymorphism (str → full rewrite, `{"append": str}` → append/create),
the cross-repo edit gate (`is_allowed_target_path` +
`target.relative_to(repo.resolve())`), and the dry-run semantics
must all stay byte-compatible.

## Research Summary

### Architecture (engdocs)

- **`engdocs/separation.md` lines 56-57, 105, 131, 197**: confirms
  `update-prompts-from-lessons` is the canonical reader of
  `$RUN_DIR/lessons-learned.md` across a time window and writer of
  prompt-fragment edits, sitting in `po-formulas-retro` (the
  org-level reflection pack). Migration aligns; no architectural
  conflict.
- **`engdocs/primitives.md` lines 169-198**: lists `update-prompts-from-lessons`
  as a feedback-loop formula that needs no new primitive — exactly
  the convergence the migration enacts (replace bespoke session
  plumbing with the existing `agent_step` primitive).
- **`engdocs/principles.md` § "Prompt authoring convention"**:
  packs author prompts as plain markdown under `agents/<role>/prompt.md`,
  with role-name decoupled from RoleRegistry / task names / verdict
  basenames. The new pattern requires a `prompt.md` (identity) +
  `task.md` (task spec) split per role (per
  `software-dev/po-formulas/po_formulas/agents/triager/`). The
  current retro pack only has `prompt.md` (combined identity +
  task) — must split.
- **`engdocs/principles.md` § "Verdict files vs prose parsing"**:
  agents write verdict JSON to `$RUN_DIR/verdicts/<step>.json`;
  flows read those files. The retro flow already does this (today
  via `read_verdict(run_dir_p, "synthesize")`); preserved.

### Existing code patterns reused

- **`prefect_orchestration.agent_step`** (496 LOC, 4ja series +
  7vs.5): the simplified one-turn primitive. Handles bead resolution
  (`<seed>.<step>.iter<N>` idempotent create), resumability (closed
  bead → return cached verdict without rerunning), task-spec
  stamping (`bd update --description=<rendered task.md>`), agent
  identity-prompt rendering, session resume via `RoleSessionStore`,
  convergence ladder (agent close → nudge → defensive force-close),
  and verdict parsing from bead close-reasons.
- **`software-dev/po-formulas/po_formulas/minimal_task.py`** (~140
  LOC): exemplar of the agent_step + plain-Python composition. Uses
  `claim_issue` at top, `agent_step(...)` for each role-step, and
  `close_issue` at bottom. The retro flow follows the same shape,
  with one twist: it must mint a synthetic seed bead per run since
  there is no user-filed issue to claim.
- **`prefect_orchestration.backend_select.select_default_backend()`**:
  honors `PO_BACKEND=cli|tmux|stub`, raises if `tmux` is requested
  but missing, and on auto/unset chooses `TmuxClaudeBackend` only
  when **both** `tmux` is on PATH AND `sys.stdout.isatty()` is True.
  Replaces today's `_select_backend()` (which has no TTY gate — see
  Risks).
- **`prefect_orchestration.beads_meta.create_child_bead`**:
  idempotent (treats "already exists" stderr as success). Used by
  `agent_step` to mint `<seed>.synthesize.iter1`. Requires the
  seed bead to be a real bd bead (`--deps parent-child:<seed>` is
  the dependency edge).
- **`prefect_orchestration.parsing.{verdicts_dir, read_verdict}`**:
  unchanged. The synthesizer agent writes
  `<run_dir>/verdicts/synthesize.json`; the flow reads it back.
- **Test fixture pattern** in
  `prefect-orchestration/tests/test_agent_step.py` (`fake_bd` +
  `fake_session` fixtures): patches
  `agent_step._bd_show / create_child_bead / close_issue / _bd_available`
  and `agent_step._build_session`, exercising orchestration without
  shelling out to bd or spawning Claude. Retro tests adopt the
  same shape.

### Design decisions + trade-offs

- **Synthetic seed bead per run** (vs `iter_n=None` synthetic-id
  trick): chosen because `create_child_bead` requires the parent to
  be a real bd bead, and `agent_step._stamp_description` shells
  `bd update <seed>.synthesize.iter1 --description=...` which
  needs the iter bead to exist. The `iter_n=None` alternative
  (operate directly on the seed) needs an existing bead anyway.
  Cost: ~52 beads/year/pack at weekly cron; mitigated by stable
  `retro-` id prefix + `-p 4` priority for filtering.
- **Two run dirs** (`<rig>/.planning/agent-step/<seed>/` for
  verdicts/sessions, `<rig>/.planning/update-prompts-from-lessons/<ts>/`
  for the operator summary): preserves the README's documented
  summary location and operator muscle memory. Slightly worse
  artifact discoverability vs consolidation; chosen for stability.
- **Drop `_select_backend()`** with deliberate behavior change on
  the auto/unset path under cron (`select_default_backend()` falls
  back to `ClaudeCliBackend` when stdout is not a TTY, while the
  current code keeps `TmuxClaudeBackend` whenever `which tmux`
  succeeds). Net: scheduled cron runs no longer create orphan tmux
  sessions; interactive `po run` still gets tmux. Improvement, but
  acknowledged not silent.
- **`dry_run` short-circuit** inside `synthesize` (vs threading
  `dry_run=` into `agent_step`): avoids the StubBackend-doesn't-write-verdict
  failure mode entirely. Mirrors today's `apply_changes_and_commit`
  dry-run shape. Net behavior tighten: dry-runs are now faster (no
  agent turn, no token spend).

### External dependencies

None new. `agent_step` is exported from `prefect_orchestration.agent_step`,
already a transitive dep of the retro pack via `prefect-orchestration`.
No `pyproject.toml` change needed.

## Success Criteria

The bead description does not list explicit ACs (the migration is
the goal). Derived from the parent (`prefect-orchestration-etl`)
bead + triage risk list:

1. `flows.py` no longer imports `AgentSession`, `ClaudeCliBackend`,
   `StubBackend`, `TmuxClaudeBackend`, or `render_template`. It
   imports `agent_step` from `prefect_orchestration.agent_step` and
   `close_issue` from `prefect_orchestration.beads_meta`.
2. `_select_backend` and `_render` are deleted.
3. The synthesizer turn is dispatched via exactly one
   `agent_step(...)` call with `agent_dir=_AGENTS_DIR / "synthesizer"`,
   `task=_AGENTS_DIR / "synthesizer" / "task.md"`, `step="synthesize"`,
   `iter_n=1`.
4. The verdict file at `verdicts/synthesize.json` has the unchanged
   shape (`{"recurring": [...], "single_occurrence": [...]}`) with
   each `recurring[i]` having the same keys as today (`theme`,
   `evidence`, `target_file`, `edit`).
5. `_apply_edit` (str → full rewrite, `{"append": str}` →
   append-or-create) is preserved verbatim.
6. `analysis.filter_recurring` and `analysis.is_allowed_target_path`
   are still called at the same boundary
   (`apply_changes_and_commit`); cross-repo gate
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
    rewiring.
11. Existing `tests/test_analysis.py` and `tests/test_deployments.py`
    pass unchanged.

**Output / demo shape:** `po run update-prompts-from-lessons
--target-pack po-formulas-software-dev --rig-path /abs/rig --since
7d` produces the same operator artifact at
`<rig>/.planning/update-prompts-from-lessons/<ts>/retro-<ts>.md`,
the same retro branch + commit in the target pack repo, and the
same `bd remember` calls — additionally writing the verdict file
under `<rig>/.planning/agent-step/retro-<slug>-<ts>/verdicts/synthesize.json`
and minting a low-priority bd bead `retro-<slug>-<ts>` that closes
with a one-line summary.

## Files to Modify/Create

Under `/home/ryan-24/Desktop/Code/personal/nanocorps/po-formulas-retro/`:

- **MODIFY** `po_formulas_retro/flows.py` — primary refactor.
  Drop `_select_backend`, `_render`, `AgentSession` /
  `ClaudeCliBackend` / `StubBackend` / `TmuxClaudeBackend` imports.
  Add `_ensure_seed_bead` private helper. Replace the `synthesize`
  task body with one `agent_step(...)` call. Add seed-bead
  lifecycle (create at top of flow, close at bottom).
- **MODIFY** `po_formulas_retro/agents/synthesizer/prompt.md` —
  rewrite to a short identity prompt mirroring
  `software-dev/po-formulas/po_formulas/agents/triager/prompt.md`
  (~20 LOC: `{{role_step_bead_id}}` + `{{role_step_close_block}}`).
- **CREATE** `po_formulas_retro/agents/synthesizer/task.md` —
  receives the bulk of the current `prompt.md` body (job spec:
  allowed target files, output contract, inputs blob). agent_step
  stamps this onto the iter bead description; the agent reads it
  via `bd show <role_step_bead_id>`. Justification: agent_step
  separates identity-prompt (small, stable) from task-spec
  (per-step, dynamic) — this is the convention every other pack
  using agent_step follows.
- **MODIFY** `tests/test_flow.py` — rewire `_patch_backend` to
  patch `prefect_orchestration.agent_step._build_session` instead
  of the now-deleted `flows._select_backend`. Add `fake_bd`
  fixture mirroring `prefect-orchestration/tests/test_agent_step.py`'s.
  Update `test_dry_run_skips_commit_and_bd_remember` to match the
  new short-circuit behavior. Add three new tests
  (seed-bead-lifecycle, str-edit branch, disallowed-target).
- **MODIFY** `tests/conftest.py` — keep existing fixtures
  (`fake_target_repo`, `fake_rig_with_runs`, `stub_synthesis_payload`).
  Add nothing required by the new pattern; the new `fake_bd`
  fixture can live in `test_flow.py` next to its consumer.

Unchanged (verify only):

- `po_formulas_retro/analysis.py` — helpers stay; called at the
  same boundary.
- `po_formulas_retro/git_ops.py` — unchanged.
- `po_formulas_retro/deployments.py` — re-imports
  `update_prompts_from_lessons`; flow object name + entry point
  preserved.
- `pyproject.toml` — no dep changes.

### Skeleton

```python
# po_formulas_retro/flows.py — new shape (excerpt)
from prefect_orchestration.agent_step import agent_step
from prefect_orchestration.beads_meta import _bd_available, close_issue
from prefect_orchestration.parsing import read_verdict, verdicts_dir

_AGENTS_DIR = Path(__file__).parent / "agents"
_FORMULA_NAME = "update-prompts-from-lessons"
_RUN_DIR_FORMULA = "update-prompts-from-lessons"


def _slug(name: str) -> str:
    """Strip non-`[A-Za-z0-9_-]` characters for bd-id-shape compat."""
    return re.sub(r"[^A-Za-z0-9_-]", "-", name).strip("-")


def _ensure_seed_bead(seed_id: str, *, title: str, description: str,
                      rig_path: Path) -> str:
    """Idempotent `bd create --id=<seed_id>`. Returns the actually-created
    seed id (may differ from the input under the bd-auto-id fallback —
    callers MUST capture: `seed_id = _ensure_seed_bead(seed_id, ...)`).
    No-op when bd missing (returns the input as-is). Falls back to bd
    auto-id if the rig rejects custom ids (see Step 0 smoke)."""
    if not _bd_available():
        return seed_id
    proc = subprocess.run(
        ["bd", "create", f"--id={seed_id}", "--title", title,
         "--description", description, "--type", "task", "-p", "4"],
        cwd=str(rig_path), capture_output=True, text=True, check=False,
    )
    if proc.returncode == 0:
        return seed_id
    stderr = (proc.stderr or "") + (proc.stdout or "")
    if "already exists" in stderr.lower():
        return seed_id
    raise RuntimeError(f"bd create {seed_id} failed: {stderr.strip()}")


@task(name="synthesize", tags=["synthesizer"])
def synthesize(seed_id: str, target_pack: str, target_repo_root: str,
               runs: list[dict], rig_path: str, dry_run: bool) -> dict:
    logger = get_run_logger()
    if not runs:
        return {"recurring": [], "single_occurrence": []}
    if dry_run:
        logger.info("dry_run=True: skipping synthesizer turn")
        return {"recurring": [], "single_occurrence": []}

    rig_p = Path(rig_path).expanduser().resolve()
    agent_step_run_dir = rig_p / ".planning" / "agent-step" / seed_id
    verdict_path = verdicts_dir(agent_step_run_dir) / "synthesize.json"

    agent_step(
        agent_dir=_AGENTS_DIR / "synthesizer",
        task=_AGENTS_DIR / "synthesizer" / "task.md",
        seed_id=seed_id, rig_path=rig_path,
        step="synthesize", iter_n=1,
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
    recurring = analysis.filter_recurring(
        [r for r in (payload.get("recurring") or []) if isinstance(r, dict)]
    )
    singles = [s for s in (payload.get("single_occurrence") or [])
               if isinstance(s, dict) and s.get("note")]
    return {"recurring": recurring, "single_occurrence": singles}
```

```markdown
# po_formulas_retro/agents/synthesizer/prompt.md — new (identity only)

You are the **synthesizer** for the retro formula on pack `{{target_pack}}`.

Your task spec lives in your role-step bead description. Read it first:

    bd show {{role_step_bead_id}}

The bead description is canonical — if anything in this prompt seems
to conflict with it, the bead wins.

{{role_step_close_block}}
```

```markdown
# po_formulas_retro/agents/synthesizer/task.md — NEW (the bulk of old prompt.md)

[Allowed target files, output contract, {{lessons_blob}}, verdict path
instructions, closing-the-bead instructions with verdict keywords
'synthesized:' or 'no-recurring:']
```

## Implementation Steps

### Step 0 — Build-step prerequisite smoke (~1 min)

Confirm the rig accepts `bd create --id=retro-…`. Some bd
configurations enforce a fixed auto-id prefix per rig.

```bash
cd /home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration
bd create --id=test-retro-id-smoke --title=x --type=task -p 4 --description=x
bd close test-retro-id-smoke --reason "smoke test"
```

**Checkpoint:** the create + close both succeed (rc=0). If create
rejects the custom id, switch `_ensure_seed_bead`'s body to the
auto-id fallback (parse the assigned id from `bd create` stdout —
roughly 10 LOC delta). Don't proceed to Step 1 until this is settled.

### Step 1 — Split the synthesizer prompt

1. Move the bulk of `po_formulas_retro/agents/synthesizer/prompt.md`
   (lines 5-60: "Your job", "Allowed target files", "Output
   contract", "Inputs", "Reply with one line summarizing…") into
   a new file `po_formulas_retro/agents/synthesizer/task.md`.
2. Append a "Closing the bead" block to `task.md` instructing the
   agent to use one of `bd close <bead> --reason "synthesized: …"`
   or `bd close <bead> --reason "no-recurring: …"`.
3. Replace `prompt.md` with the short identity prompt (skeleton
   above). Reference `{{role_step_bead_id}}` and
   `{{role_step_close_block}}`.

**Checkpoint:** `wc -l po_formulas_retro/agents/synthesizer/{prompt,task}.md`
shows `prompt.md ≤ 25 lines` and `task.md ≥ 50 lines`. Diff against
old `prompt.md` shows the union covers every original line.

### Step 2 — Refactor `flows.py`

1. Replace the `AgentSession` / `Claude*Backend` / `render_template`
   imports with `from prefect_orchestration.agent_step import agent_step`
   and `from prefect_orchestration.beads_meta import close_issue, _bd_available`.
2. Delete `_select_backend`, `_render`, and the `shutil` import (if
   unused after deletion).
3. Add `_slug(name)` and `_ensure_seed_bead(seed_id, title, desc, rig_path)`
   private helpers.
4. Replace the `synthesize` task body per the skeleton.
5. In `update_prompts_from_lessons` (the `@flow`):
   - Mint `requested_seed_id = f"retro-{_slug(target_pack)}-{timestamp}"`.
   - **Capture the return value** of `_ensure_seed_bead` —
     `seed_id = _ensure_seed_bead(requested_seed_id, ..., rig_path=rig)`
     — so the bd-auto-id fallback (Step 0 / §1 / Risk #10) wires the
     actually-created id through to subsequent calls. Skip on the
     refusal path.
   - Pass `seed_id` to `synthesize(...)`.
   - At the end (after `write_summary`), call
     `close_issue(seed_id, notes=f"complete: ...", rig_path=rig)`.
   - Add `seed_id` to the returned dict.
6. Keep `_apply_edit`, `_format_lessons_blob`, `_bd_remember`,
   `_utc_timestamp`, and the other `@task`s unchanged.

**Checkpoint:** `grep -E "AgentSession|render_template|_select_backend|_render\b|TmuxClaudeBackend|StubBackend|ClaudeCliBackend" po_formulas_retro/flows.py` returns nothing. `grep -c "agent_step(" po_formulas_retro/flows.py` returns `1`.

### Step 3 — Rewire tests

1. In `tests/test_flow.py`:
   - Add a `fake_bd` fixture (mirror of
     `prefect-orchestration/tests/test_agent_step.py::fake_bd`).
     In-memory dict for bead state; patches
     `prefect_orchestration.agent_step._bd_show`, `create_child_bead`,
     `close_issue`, `_bd_available`. Patches `subprocess.run`
     **smartly**: when `argv[:3] == ["bd", "update", <id>]` and
     `--description` is in argv, capture the description value into
     `fake_bd[<id>]["description"]`; for any other shellout, no-op.
   - Replace `_patch_backend` with one that patches
     `agent_step_mod._build_session` to return a `_FakeSession`.
     The fake session's `prompt(text, **kw)`:
     - locates the iter bead in `fake_bd` (most recently created
       `*.synthesize.iter1`)
     - regex-extracts `verdict_path` from
       `fake_bd[<iter_bead>]["description"]`
     - writes the canned synthesis payload to that path
     - marks the iter bead `status="closed"` with reason
       `"synthesized: <theme-count> recurring themes"`
     so agent_step's convergence ladder takes the agent-closed path.
2. Update existing tests:
   - `test_three_matching_runs_produce_prompt_edit`: still asserts
     verdict shape, edited file contents, branch creation, summary
     path, `bd_remember` call. New: also assert
     `result["seed_id"]` matches `^retro-po-formulas-fake-\d{8}T\d{6}Z$`.
   - `test_main_branch_creates_retro_branch`: unchanged.
   - `test_feature_branch_commits_in_place`: unchanged.
   - `test_refuses_on_non_editable_target`: swap "backend should not
     be selected" → "agent_step's `_build_session` should not be
     invoked"; rest unchanged.
   - `test_dry_run_skips_commit_and_bd_remember`: replace
     `_patch_backend(monkeypatch, _CannedBackend(stub_synthesis_payload))`
     with a fake whose `prompt()` raises `AssertionError("dry-run
     should not invoke the agent")`. Assert no commit / no
     `bd_remember` / summary present / `result["recurring"] == []`.
3. New tests:
   - `test_seed_bead_created_and_closed` — assert `_ensure_seed_bead`
     called once with `seed_id` matching `retro-<slug>-<ts>`, and
     `close_issue(seed_id, ...)` called once with notes containing
     the recurring count.
   - `test_str_edit_branch` — extend payload with one item whose
     `edit` is a string (full rewrite); assert
     `target.read_text() == <string>`.
   - `test_disallowed_target_filtered` — inject recurring item with
     `target_file="../../../etc/passwd"`; assert it's NOT in
     `commit["edited_files"]`.

**Checkpoint:** `cd po-formulas-retro && uv run python -m pytest
tests/test_flow.py -v` is green (4 old + 3 new = 7 tests pass).

### Step 4 — Cross-repo regression check

```bash
cd po-formulas-retro && uv run python -m pytest tests/ -v
```

**Checkpoint:** all `test_analysis.py` and `test_deployments.py`
tests pass unchanged.

### Step 5 — Deployment + import smoke

```bash
cd po-formulas-retro && uv run python -c \
  "from po_formulas_retro.deployments import register; \
   d = register()[0]; print(d.name, d.flow_name)"
```

**Checkpoint:** prints `retro-weekly update-prompts-from-lessons`.

### Step 6 — Optional: env-override smoke

```bash
cd po-formulas-retro && PO_BACKEND=stub uv run python -m pytest \
  tests/test_flow.py::test_three_matching_runs_produce_prompt_edit -v
```

**Checkpoint:** test passes (the fake-session monkeypatch on
`_build_session` preempts StubBackend, but the env-var should not
break the wire-up).

## Verification Strategy

| Criterion | Verification Method | Concrete Check |
|---|---|---|
| #1 — `flows.py` import surface tightened | grep | `grep -E "AgentSession\|render_template\|TmuxClaudeBackend\|StubBackend\|ClaudeCliBackend" po_formulas_retro/flows.py` returns no matches; `grep "from prefect_orchestration.agent_step import agent_step" po_formulas_retro/flows.py` returns one line |
| #2 — `_select_backend` / `_render` deleted | grep | `grep -E "def _select_backend\|def _render\b" po_formulas_retro/flows.py` returns no matches |
| #3 — One `agent_step(...)` call w/ correct kwargs | grep + visual | `grep -c "agent_step(" po_formulas_retro/flows.py` returns `1`; visual inspection of the call shows `agent_dir=_AGENTS_DIR / "synthesizer"`, `task=_AGENTS_DIR / "synthesizer" / "task.md"`, `step="synthesize"`, `iter_n=1` |
| #4 — Verdict file shape preserved | unit test | `test_three_matching_runs_produce_prompt_edit` reads `result["recurring"][0]["target_file"]`, `["theme"]`, `["evidence"]`, `["edit"]` — all present and well-typed |
| #5 — `_apply_edit` polymorphism preserved | unit test (new) | `test_str_edit_branch` asserts `target.read_text() == <full new contents>` for the str branch; existing test covers `{"append": ...}` branch |
| #6 — Cross-repo gate intact | unit test (new) | `test_disallowed_target_filtered` injects `target_file="../../../etc/passwd"`; asserts `"etc/passwd" not in str(commit["edited_files"])` |
| #7 — `bd remember` routing | unit test | `test_three_matching_runs_produce_prompt_edit` asserts `bd_calls == [f"[{target_pack}] scoped git add avoids worker collisions"]` |
| #8 — Dry-run skip semantics | unit test | `test_dry_run_skips_commit_and_bd_remember` asserts `pre_sha == post_sha`, `commit["commit_sha"] is None`, `bd_calls == []`, summary exists, AND fake session's `prompt()` was never invoked |
| #9 — Deployment importability | smoke | `python -c "from po_formulas_retro.deployments import register; print(register()[0].name)"` prints `retro-weekly` |
| #10 — Existing tests rewired green | pytest | `cd po-formulas-retro && uv run python -m pytest tests/test_flow.py -v` — all four original tests pass under the new harness |
| #11 — Sibling tests unchanged green | pytest | `cd po-formulas-retro && uv run python -m pytest tests/ -v` — full pack suite green |
| Seed-bead lifecycle (new behavior) | unit test (new) | `test_seed_bead_created_and_closed` asserts `_ensure_seed_bead` called once with id matching `^retro-[A-Za-z0-9_-]+-\d{8}T\d{6}Z$`, and `close_issue` called once at flow exit with notes containing the recurring count |

All criteria are auto-verifiable. No manual steps.

## Test Plan

**Layer**: unit only (under `po-formulas-retro/tests/`). The retro
pack has no `tests/e2e/`, no `tests/playwright/`, and no UI.
Triage's `has_ui=false` and `is_docs_only=false` confirm.

### Files

- `tests/test_flow.py` — modify 4 existing tests + add 3 new
  (`test_seed_bead_created_and_closed`, `test_str_edit_branch`,
  `test_disallowed_target_filtered`).
- `tests/conftest.py` — keep as-is; new `fake_bd` fixture lives in
  `test_flow.py` next to its consumers.
- `tests/test_analysis.py` — unchanged; run to confirm.
- `tests/test_deployments.py` — unchanged; run to confirm.

### Commands

```bash
cd /home/ryan-24/Desktop/Code/personal/nanocorps/po-formulas-retro
uv run python -m pytest tests/ -v
# Optional env-override smoke (does NOT exercise real Claude):
PO_BACKEND=stub uv run python -m pytest tests/test_flow.py -v
```

The parent rig (`prefect-orchestration`) has its own test suite at
`/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/`
which is **out of scope** for this issue (we don't touch core).
Baseline at `.planning/software-dev-full/prefect-orchestration-etl/baseline.txt`
shows 762 passed / 1 skipped — those numbers should be unchanged
post-build (since the build doesn't touch `prefect-orchestration/`).

## Risks

1. **Seed-bead creation pollutes bd.** Every retro run mints a
   `retro-<slug>-<timestamp>` bead. ~52/year/pack at weekly cron.
   Mitigation: `-p 4` (low priority) + stable `retro-` prefix make
   bulk filter / archive trivial. Not a blocker.

2. **Two run dirs** (`<rig>/.planning/agent-step/<seed>/` for
   verdicts/sessions vs `<rig>/.planning/update-prompts-from-lessons/<ts>/`
   for the operator summary). Slightly worse artifact discoverability.
   Chosen for stability — README example + operator scripts that
   scan `update-prompts-from-lessons/` keep working. Reviewers who
   want consolidation: one-line change to point `flow_run_dir` at
   the agent_step run dir.

3. **Verdict file location migration.** Today:
   `<rig>/.planning/update-prompts-from-lessons/<ts>/verdicts/synthesize.json`.
   New: `<rig>/.planning/agent-step/<seed_id>/verdicts/synthesize.json`.
   The summary file path (visible artifact) does NOT change. README
   doesn't reference the verdict path. Internal-only break.

4. **`render_template` overlay differences — verified clean.** `ls
   po_formulas_retro/agents/synthesizer/` shows only `prompt.md`,
   so agent_step's optional `identity.toml` / `memory/MEMORY.md`
   overlays are inert. No surprise merge.

5. **`{{verdict_path}}` substitution path.** `task.md` includes
   `{{verdict_path}}`; agent_step's `_safe_substitute` is permissive
   (unknown vars stay literal). We pass `verdict_path` in `ctx`, so
   substitution succeeds. If a future refactor renames the ctx key,
   the agent silently writes nowhere. Mitigation: flag in `task.md`
   that the path is critical; consider asserting non-empty after
   substitution as a future hardening (out of scope here).

6. **Test backend stubbing surface change.** Tests now patch
   `agent_step._build_session` (a private helper). If
   `prefect_orchestration` ever refactors `agent_step` internals,
   the tests break. Mitigation: this matches
   `tests/test_agent_step.py`'s established pattern; the core
   repo's tests are the upstream contract for the patching surface,
   so they fail in lock-step.

7. **Cross-repo edit gate** (triage risk #8). The refactor must NOT
   loosen `is_allowed_target_path` or the
   `target.relative_to(repo.resolve())` check — both still live in
   `apply_changes_and_commit`, unchanged. New
   `test_disallowed_target_filtered` makes this explicit.

8. **`StubBackend` in dry-run path — solved by short-circuit.**
   `if dry_run: return {"recurring": [], "single_occurrence": []}`
   fires BEFORE `agent_step`, so the StubBackend doesn't-write-verdict
   failure mode is never exercised. Behavior tighten vs today: no
   agent turn at all under dry-run. Test
   `test_dry_run_skips_commit_and_bd_remember` updated to match.

9. **Backend selection behavior change on non-TTY auto path.**
   Today's `_select_backend()` chooses `TmuxClaudeBackend` whenever
   `which tmux` is truthy. `select_default_backend()` adds a
   `sys.stdout.isatty()` gate (`backend_select.py:77-80`) — under
   cron / Prefect worker (non-TTY), retro switches from
   `TmuxClaudeBackend` to `ClaudeCliBackend`. **Improvement** (tmux
   without a controlling terminal is fragile), but observable for
   operators. Mitigation: one-line README note explaining
   `PO_BACKEND=tmux` to opt back in. Not silent — explicitly
   acknowledged.

10. **bd custom-id acceptance.** Some rig configurations enforce
    a fixed auto-id prefix per rig and reject `bd create --id=retro-…`.
    Step 0 smoke catches this before code lands. Fallback path
    (~10 LOC delta in `_ensure_seed_bead`): bd auto-id, parse
    assigned id from stdout. Plan call sites unchanged.

11. **`PO_FORMULA_MODE` interaction.** `software_dev_full` reads
    `PO_FORMULA_MODE=legacy|graph` to switch implementations. Retro
    does NOT need this gate — there's no graph-mode for retro and no
    long-term legacy body to keep around. Don't add a mode env var
    here.

12. **No new dependencies.** `agent_step` is exported from
    `prefect_orchestration.agent_step`, already a transitive dep
    via `prefect-orchestration`. No `pyproject.toml` change.
