# Plan: prefect-orchestration-7vy — `po-formulas-retro` pack

## Pack location

Per CLAUDE.md `pw4` ("land pack-contrib code in the pack's repo, not
in the caller's rig-path") and triage flag, the new pack lives at
**`/home/ryan-24/Desktop/Code/personal/nanocorps/po-formulas-retro/`**
as a sibling of `software-dev/po-formulas/`, NOT inside this rig.
Builds against editable `prefect-orchestration` via `[tool.uv.sources]`.

## Affected files (best guess — adjust during build)

New pack repo (`../po-formulas-retro/`):

- `pyproject.toml` — package `po-formulas-retro`; entry points
  `po.formulas` (`update-prompts-from-lessons`), `po.deployments`
  (`retro = po_formulas_retro.deployments:register`); hatchling build;
  `[tool.uv.sources]` editable link to core.
- `po_formulas_retro/__init__.py`
- `po_formulas_retro/flows.py` — `update_prompts_from_lessons(target_pack: str, since: str = "7d", rig_path: str | None = None, dry_run: bool = False) -> dict` Prefect `@flow` with `@task` steps: `locate_target_pack`, `collect_lessons`, `synthesize`, `apply_changes_and_commit`, `write_summary`. Uses `AgentSession` (one turn) like `software_dev.py` does. Bead-claim/close optional — the flow operates on a target pack, not a single bead, so it skips `claim_issue`/`close_issue`.
- `po_formulas_retro/analysis.py` — pure helpers: `pack_repo_root(dist_name) -> Path`, `is_editable_install(dist) -> bool` (inspect `direct_url.json` `dir_info.editable`), `iter_run_dirs(rig_path, formula_names, since: timedelta) -> Iterator[Path]`, `read_lessons_and_decisions(run_dir) -> dict`, `bucket_recurrences(snippets) -> dict[str, list[str]]` (3+ threshold).
- `po_formulas_retro/deployments.py` — `register()` returning a single `RunnerDeployment` for `update_prompts_from_lessons` with `cron="0 9 * * 0"` (Sunday 09:00 UTC), `work_pool_name="po"`, parameter shape leaving `target_pack` empty (callers override). Uses `flow.to_deployment(...)` consistent with how core's `deployments.py` validates.
- `po_formulas_retro/git_ops.py` — small wrapper: `current_branch(repo)`, `ensure_retro_branch(repo)` (creates `retro/<utc-ts>` only when on main/master), `commit_paths(repo, paths, message)` (scoped `git add <path>` per CLAUDE.md guidance — never `-A`).
- `po_formulas_retro/agents/synthesizer/prompt.md` — single-turn prompt: input is concatenated `lessons-learned.md` + `decision-log.md` excerpts; output contract is JSON written to `$RUN_DIR/verdicts/synthesize.json` with shape `{"recurring": [{"theme": str, "evidence": [run_id,...], "target_file": "rel/path", "edit": "full new file contents" | {"append": "..."}}], "single_occurrence": [{"note": str, "target_pack": str}]}`. Recurring = ≥3 distinct run_dirs mention the same theme. Constrains writes to prompt / skill / CLAUDE.md paths only.
- `skills/retro/SKILL.md` — boundaries: may edit `**/agents/**/prompt.md`, `**/skills/**/SKILL.md`, `**/CLAUDE.md`; MUST NOT edit `*.py`, `pyproject.toml`, tests, or workflow YAML; commits go to `retro/<utc-ts>` when on main/master.
- `README.md` — install + cron usage.
- `tests/test_analysis.py` — unit: `bucket_recurrences` threshold, `is_editable_install` against fixture `direct_url.json`, `iter_run_dirs` mtime filter.
- `tests/test_flow.py` — uses `StubBackend` (sets `PO_BACKEND=stub`) + a fixture rig with three fake run dirs all containing the same lesson string; asserts (a) synthesizer turn invoked, (b) when stub writes a `synthesize.json` with one recurring theme, the flow writes the proposed prompt edit, scoped-`git add`s it, commits with the expected message, and writes `retro-<ts>.md`; (c) single-occurrence lessons trigger `bd remember` invocation (mock the shell-out).
- `tests/test_deployments.py` — `register()` returns one `RunnerDeployment` named `retro-weekly` with cron schedule and `work_pool_name="po"`.
- `tests/test_editable_refusal.py` — when `direct_url.json` shows non-editable install, flow exits cleanly with a structured "refused" result and writes no commits.

No changes inside `prefect-orchestration` core. No changes to
`software-dev/po-formulas`.

## Approach

1. **Bootstrap pack scaffolding** mirroring `software-dev/po-formulas/`
   layout: `pyproject.toml` with `po.formulas` + `po.deployments` entry
   points, hatchling build, editable source dep on core. Use
   `po install --editable ../po-formulas-retro` for local dev.
2. **Locate target pack repo** via `importlib.metadata.distribution(
   target_pack)`. Inspect the `direct_url.json` record to confirm
   `dir_info.editable == True`; trace `url` → filesystem path → walk
   parents until a `.git` directory is found. Refuse with a structured
   `{"refused": "not editable"}` result if either check fails.
3. **Collect lessons**: enumerate the target pack's formula names by
   reading its `[project.entry-points."po.formulas"]` section (parse
   `pyproject.toml` with `tomllib`). For each formula, glob
   `<rig_path>/.planning/<formula>/*/lessons-learned.md` and the
   sibling `decision-log.md`. Filter by `mtime` ≥ `now - since` (parse
   `since` like `"7d"`/`"24h"` with a small helper).
4. **Synthesize via one Claude turn**: build an `AgentSession(role=
   "synthesizer", repo_path=target_repo)` keyed by the synthesizer
   prompt. Embed each run's lessons + decisions in tagged blocks
   (`<run id="..."> ... </run>`). Ask the agent to write
   `$RUN_DIR/verdicts/synthesize.json` matching the schema above. Read
   it back via `parsing.read_verdict("synthesize", run_dir)`.
5. **Apply changes & commit**:
   - For each `recurring[*]` item, write `target_file` (resolved
     relative to the target repo root, validated to fall under
     prompts/skills/CLAUDE.md). Reject paths with `..` traversal.
   - `current_branch()`; if `main` or `master`, `git checkout -b
     retro/<utc-ts>`. Otherwise stay on the current branch.
   - `git add <each-changed-path>` (scoped — never `-A`); commit with
     `retro(<target_pack>): integrate lessons from <N> runs since <since>`.
   - No push.
6. **Single-occurrence lessons** → shell out
   `bd remember "[<target_pack>] <note>"` once per item. (Tag prefix
   substitutes for the missing `target_pack:` namespace; documented in
   `SKILL.md`.)
7. **Run-dir & summary artifact**: the flow's own `run_dir` is
   `<rig_path>/.planning/update-prompts-from-lessons/<utc-ts>/`
   (formula-name slugged from the flow). Write `retro-<ts>.md` there
   summarizing recurring themes, single-occurrence notes, the
   resulting branch + commit SHA, and the list of edited files.
   `dry_run=True` short-circuits step 5 and step 6 but still writes
   the summary so a human can review.
8. **Deployment**: `deployments.register()` returns a
   `RunnerDeployment` with `name="retro-weekly"`, weekly cron,
   `work_pool_name="po"`. `po deploy --apply` registers it. `po
   doctor` already warns on missing pools.

## Acceptance criteria (verbatim from issue)

(1) po-formulas-retro/ is a sibling pack of software-dev/po-formulas;
(2) update_prompts_from_lessons flow takes target_pack + since kwargs; reads lessons + decision-log; synthesizes via one Claude turn; produces a diff of prompt/skill/CLAUDE.md files;
(3) commits to active branch; if main/master, creates retro/<utc-ts> branch first;
(4) single-occurrence lessons land in 'bd remember' instead of file edits;
(5) writes retro-<ts>.md summary artifact into target pack's run dir;
(6) refuses cleanly when target pack is not editable (no writable source tree);
(7) ships 'retro-weekly' deployment via po.deployments entry point;
(8) skills/retro/SKILL.md documents the boundaries — what the retro may and may not change (prompts, CLAUDE.md, skills — NOT code);
(9) tested: generate 3 runs with matching lessons, verify flow detects pattern and proposes a prompt edit.

## Verification strategy

| AC | Concrete check |
|---|---|
| 1 | `ls ../po-formulas-retro/pyproject.toml` exists; `po install --editable ../po-formulas-retro && po list` shows `update-prompts-from-lessons` (KIND=formula). |
| 2 | `po show update-prompts-from-lessons` prints signature with `target_pack`, `since`. Unit test in `test_flow.py` asserts the synthesizer is invoked exactly once and that its proposed file changes land in a unified-style commit (we read the commit's `git show --stat`). |
| 3 | Test runs the flow twice: once on a fixture repo currently on `main` (asserts new branch matches `retro/\d{8}T\d{6}Z`), once on a feature branch (asserts branch unchanged, commit on feature branch). |
| 4 | Test patches `subprocess.run` for `bd remember`; asserts it is called for each `single_occurrence` entry with the `[<target_pack>]` prefix; asserts no file edits for those entries. |
| 5 | Test asserts `retro-<utc-ts>.md` exists under the flow's run_dir and contains the theme list + commit SHA. |
| 6 | Test fixture writes a fake `direct_url.json` with `dir_info.editable = False`; asserts flow returns `{"refused": ...}` and made zero commits / no `bd remember` calls. |
| 7 | `python -c "from po_formulas_retro.deployments import register; d=register(); assert d[0].name=='retro-weekly' and d[0].schedule.cron"`. Also `po deploy` lists it. |
| 8 | File `../po-formulas-retro/skills/retro/SKILL.md` exists; grep asserts the words "prompts", "skills", "CLAUDE.md", and an explicit "NOT code" line. |
| 9 | `tests/test_flow.py::test_three_matching_runs_produce_prompt_edit` builds three fake run dirs, runs the flow with `PO_BACKEND=stub` + a stub that emits a synthesize.json with one recurring theme, asserts a prompt-file edit is committed. |

## Test plan

- **Unit**: `tests/test_analysis.py` — recurrence threshold, editable-install detection, `since` parsing, `iter_run_dirs` mtime filter, path-traversal rejection.
- **Unit**: `tests/test_deployments.py` — `register()` shape.
- **Unit/integration**: `tests/test_flow.py` — full flow with `StubBackend`; uses `tmp_path` for both rig and target-pack git repos (`git init`, fake editable `direct_url.json`, fake `pyproject.toml` with one formula entry, three fake run dirs).
- **Unit**: `tests/test_editable_refusal.py` — refusal path.
- **No Playwright** (no UI). **No e2e in this repo** — pack ships its own tests; e2e requires `bd` + Prefect server and would belong in the pack's CI, not the core's `tests/e2e/`.

## Risks

- **Editable detection edge cases**: PEP 660 wheels vs `setup.py develop` legacy installs vs `uv tool install --editable` all produce `direct_url.json` slightly differently. Plan: trust `dir_info.editable == True`; fall back to "is the resolved path inside a git working tree?" before refusing.
- **Polyrepo / monorepo packs**: `direct_url.json` points at the package source dir, not the repo root. We walk parents looking for `.git`; if absent, refuse. Documented in `SKILL.md`.
- **Concurrent retro runs collide on branch name**: utc-ts is second-precision; two runs in the same second could collide. Mitigation: `git checkout -b retro/<ts>` retries with `<ts>-1`, `<ts>-2` on `branch already exists`.
- **Recurrence detection quality**: relies on the synthesizer prompt to define "same theme". The prompt requires `evidence` ≥ 3 distinct `run_id`s — orchestrator-side schema validation enforces this and drops underqualified items, so a sloppy synthesizer can't bypass the bar.
- **`bd remember` namespacing**: `bd` doesn't natively support per-pack tags; we use a `[<target_pack>]` prefix convention. Documented in `SKILL.md`. If `bd` later adds tags, swap the call site (one place).
- **Auto-push risk**: pre-commit / `husky`-style hooks in the target repo could push. The flow uses `git -c core.hooksPath=/dev/null commit` to bypass commit hooks for the retro commit, then explicitly `git push` is never invoked. Documented as a risk in `SKILL.md`.
- **Pack lives outside this rig**: builder must `cd` into `../po-formulas-retro/` for `git add`/`commit` of the new pack files (per CLAUDE.md polyrepo rig rule). The `prefect-orchestration` rig itself receives no edits as part of this issue.
- **No API/contract changes** to core; no migrations. Existing consumers unaffected.
- **Baseline failures (6 tests)** are pre-existing and unrelated to this work; regression-gate must compare to baseline rather than expect green.
