# Plan: prefect-orchestration-4ja.3 — agents/<role>/prompt.md layout

## Affected files

Pack repo (`/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/`):

- `po_formulas/prompts/*.md` — **moved** to `po_formulas/agents/<role>/prompt.md` (git mv, content verbatim).
- `po_formulas/software_dev.py` — every `render("<step>", …)` call updated to the new role identifier; `_PROMPTS_DIR` renamed to `_AGENTS_DIR` and pointed at `po_formulas/agents`.
- `po_formulas/epic.py` — grep + update if it touches prompts (likely none — verify).
- `pyproject.toml` (pack) — `package-data` / `include` pattern updated so `agents/**/prompt.md` ships in the wheel (was `prompts/*.md`). Confirm by `uv build` and inspecting the sdist/wheel manifest.
- `mail.md` is documentation, not loaded via `render()`. Leave in place under `po_formulas/mail.md` (or `docs/mail.md`); do **not** create an `agents/mail/` folder for it. Confirm during build with `grep -r mail.md`.

Core repo (`/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/`):

- `prefect_orchestration/templates.py` — `render_template()` learns the new layout. Keep the existing `name: str` positional arg (now interpreted as **role**); resolve `<prompts_dir>/<role>/prompt.md`. The caller still passes a "prompts dir" — rename the parameter to `agents_dir` for clarity but keep positional compat. Hyphenated role names (`plan-critic`, `regression-gate`) work as-is since they're just directory names.
- `CLAUDE.md` — add a short "Prompt layout" section under the pack-contrib block showing the `agents/<role>/prompt.md` tree and noting "no Jinja, no fragments — duplicate before sharing".
- `engdocs/principles.md` — if it has a "Prompt authoring convention" section (per triage), update the example tree there too.

## Approach

1. **Inventory and rename map.** Map every existing `prompts/<file>.md` to its `agents/<role>/prompt.md` target:

   | old `render(name)` | old file | new role dir |
   |---|---|---|
   | `triager` | `triager.md` | `agents/triager/prompt.md` |
   | `baseline` | `baseline.md` | `agents/baseline/prompt.md` |
   | `plan` | `plan.md` | `agents/planner/prompt.md` |
   | `critique_plan` | `critique_plan.md` | `agents/plan-critic/prompt.md` |
   | `build` | `build.md` | `agents/builder/prompt.md` |
   | `review` | `review.md` | `agents/build-critic/prompt.md` |
   | `lint` | `lint.md` | `agents/linter/prompt.md` |
   | `test` | `test.md` | `agents/tester/prompt.md` |
   | `regression_gate` | `regression_gate.md` | `agents/regression-gate/prompt.md` |
   | `deploy_smoke` | `deploy_smoke.md` | `agents/deploy-smoke/prompt.md` |
   | `review_artifacts` | `review_artifacts.md` | `agents/review-artifacts/prompt.md` |
   | `verification` | `verification.md` | `agents/verifier/prompt.md` |
   | `ralph` | `ralph.md` | `agents/ralph/prompt.md` |
   | `docs` | `docs.md` | `agents/documenter/prompt.md` |
   | `demo_video` | `demo_video.md` | `agents/demo-video/prompt.md` |
   | `learn` | `learn.md` | `agents/learn/prompt.md` |

   Note: the issue design lists a separate `reviewer/` agent, but the current flow has only one build-iter critic (`render("review")`) — that step is conceptually the **build-critic**. There is no orphan "reviewer" prompt today; if a future agent appears, it gets its own dir then. Flag in decision log.

   `mail.md` stays out of `agents/` (it's a helper doc, not an agent prompt).

2. **Move files with `git mv`** so history is preserved. Use one `git mv` per file rather than a regex script.

3. **Update `templates.render_template()`** to read `<dir>/<role>/prompt.md`. Old call sites pass the same positional `name`; the only behavioral change is the resolved path. Update the docstring and the parameter name from `prompts_dir` → `agents_dir` (keep positional, no kwarg break). Tests in `tests/test_templates.py` (if present) updated to write `agents/<role>/prompt.md` fixtures.

4. **Update `software_dev.py`** render call strings to the new role identifiers (`"plan"` → `"planner"`, `"critique_plan"` → `"plan-critic"`, `"build"` → `"builder"`, `"review"` → `"build-critic"`, `"lint"` → `"linter"`, `"test"` → `"tester"`, `"regression_gate"` → `"regression-gate"`, `"deploy_smoke"` → `"deploy-smoke"`, `"review_artifacts"` → `"review-artifacts"`, `"verification"` → `"verifier"`, `"docs"` → `"documenter"`, `"demo_video"` → `"demo-video"`). Rename `_PROMPTS_DIR` → `_AGENTS_DIR`.

5. **Do NOT change** `RoleRegistry` keys (`"triager"`, `"builder"`, `"critic"`, `"tester"`, `"releaser"`, `"verifier"`, `"cleaner"`) or task `name=`/`tags=` strings. Those are runtime role-class identifiers tied to per-role Claude session UUIDs in `metadata.json` and tmux session names — renaming would orphan in-flight sessions. The plan only renames *prompt-file lookup keys*, not `RoleRegistry` keys, task names, verdict-file basenames (`plan-critique-iter-N`, `review-iter-N`, etc.), or tmux session naming. Triage worry about session-uuid orphaning is mitigated by this scope limit.

6. **Verdict-file basenames** are *not* prompt names. Leave `verdicts/plan-critique-iter-N.json`, `review-iter-N.json`, etc., as-is.

7. **Package data.** Update the pack's `pyproject.toml` so `agents/**/prompt.md` is included. Verify with `uv build && unzip -l dist/*.whl | grep prompt.md`.

8. **Reinstall the editable tool** so the new package data is picked up:
   `uv tool install --force --editable . --with-editable ../../../software-dev/po-formulas` (per CLAUDE.md).

9. **Docs.** Add layout block to `CLAUDE.md` under the existing prompt section; mirror in `engdocs/principles.md` if it has a prompt-authoring section.

## Acceptance criteria (verbatim from issue)

1. Every existing prompt moved to `agents/<role>/prompt.md` with no content changes.
2. `render_template` + flow code paths updated.
3. Existing `software-dev-full` run still works end-to-end (verify with stub backend dry-run).
4. Documented in CLAUDE.md with the layout example.
5. No new deps, still plain markdown.

## Verification strategy

- **AC1 (verbatim move):** for each old/new pair, `diff <(git show HEAD:po_formulas/prompts/<old>.md) po_formulas/agents/<role>/prompt.md` returns empty. Roll up into a one-liner script in the build phase.
- **AC2 (loader + flow updated):** `grep -rn "prompts/" po_formulas/` returns only `mail.md` doc references; `grep -rn 'render("' po_formulas/software_dev.py` shows new role names; `python -c "from prefect_orchestration.templates import render_template; render_template(Path('agents'), 'triager', issue_id='x', ...)"` resolves.
- **AC3 (e2e dry-run):**
  ```
  PO_BACKEND=stub po run software-dev-full \
    --issue-id <test-bead> --rig prefect-orchestration \
    --rig-path /home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration \
    --dry-run
  ```
  Expect exit 0 and a fully populated `<rig>/.planning/software-dev-full/<id>/` (triage.md, plan.md, etc.). If `--dry-run` is not yet a flag, use the existing stub-backend codepath that the e2e tests rely on (see `tests/e2e/test_po_*` for the pattern). Also run `uv run python -m pytest` — full suite (176 tests baselined green) must remain green.
- **AC4 (CLAUDE.md):** `grep -A 20 "agents/" CLAUDE.md` shows the new tree.
- **AC5 (no new deps):** `git diff pyproject.toml` (both repos) shows no new `[project.dependencies]` entries; `grep -rn "jinja\|Jinja" .` returns nothing in modified code paths.

## Test plan

- **Unit:** `tests/test_templates.py` — rewrite fixtures to use `agents/<role>/prompt.md`; add a case for hyphenated role (`plan-critic`) and missing role (KeyError → FileNotFoundError surfaces cleanly with the role name in the message).
- **E2E:** existing `tests/e2e/test_po_*` suites already drive the CLI with stub-backend roundtrips. They should pass unmodified — that is the integration check that the new layout loads end-to-end.
- **Playwright:** N/A (no UI).
- **Manual smoke:** the dry-run command in AC3.

## Risks

- **Polyrepo edits.** Most file moves and code changes happen in `../software-dev/po-formulas/`, *outside* the rig-path. Builder must `cd` into the pack's git ancestor before `git add`/`commit` (per CLAUDE.md "Polyrepo rigs" guidance). Verify the sibling pack is checked out and writable before starting.
- **Package data drop.** If `pyproject.toml` `package-data` glob isn't updated, the wheel ships without the new `agents/` tree and runtime resolution explodes only after `uv tool install --force`. Mitigation: build the wheel and inspect contents during verification.
- **Editable install staleness.** Entry-point metadata is baked at install time. After moving files, `uv tool install --force` is required even though the code is editable. The rename of `_PROMPTS_DIR` doesn't change entry points, but the `package-data` change does — re-install once.
- **Verdict-file / task-name drift.** Easy to over-rename and accidentally touch `read_verdict(...)` keys or `RoleRegistry` keys. Plan explicitly limits scope to prompt-file lookup keys; reviewer should grep for stray renames.
- **Hyphen vs underscore.** Role identifiers now contain hyphens (`plan-critic`); they're only used as path segments and `render()` strings — not Python identifiers — so this is safe. Confirm no place tries to `getattr` by role name.
- **No git remote on this rig.** Don't `git push` after committing; just stage and commit locally per CLAUDE.md.
- **Backwards-compat for other packs.** Only `po-formulas-software-dev` is known to consume `render_template` today (per CLAUDE.md "Installed at runtime"). The signature change is layout-only (positional `name` still accepted) — no kwarg break — so no shim needed.
