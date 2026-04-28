# Implementation Summary: prefect-orchestration-9r2

## Issue
Skill evals convention + runner formula — define a per-skill
`evals/cases.yaml` + `rubrics.yaml` + `reports/` layout, ship a core
`skill-evals` formula that runs a pack-shipped skill against its eval
suite using `pydantic_evals.evaluators.LLMJudge`, emit machine + human
reports plus a verdict file, and emit Logfire spans when
`PO_TELEMETRY=logfire`.

## What Was Implemented

### Files Created

| File | Purpose |
|------|---------|
| `prefect_orchestration/skill_evals.py` | Core formula — pack/skill resolution, YAML IO, agent driver, real + stub judging, report writers, verdict writer, telemetry spans, Prefect `@flow` entrypoint. |
| `prefect_orchestration/skill_evals_schema.py` | Pydantic models (`CasesFile`, `RubricsFile`, `CriterionResult`, `CaseResult`, `SkillEvalsVerdict`). |
| `engdocs/skill-evals.md` | Convention doc — directory layout, full annotated YAML schemas, CLI reference, telemetry attrs, verdict shape, install hint, cost notes. |
| `tests/test_skill_evals.py` | 25 unit tests covering schema parsing, pack discovery (editable + wheel + missing pack + missing skill), tier/case filters, judge construction (real `LLMJudge` + friendly `[evals]`-missing error), stub judging (deterministic + no-import-of-pydantic-evals), `drive_skill` against StubBackend, judge-result coercion (dict + bool + clamping), end-to-end flow (dry-run reports + run-dir verdict + tier filter + threshold gating), real-judges path (mocked `evaluate`), telemetry recording fake + Noop default, EP discoverability, asyncio.gather plumbing. |
| `tests/fixtures/skill_evals/sample-pack/skills/sample/SKILL.md` | Synthetic skill fixture. |
| `tests/fixtures/skill_evals/sample-pack/skills/sample/evals/cases.yaml` | Two-case fixture covering smoke + regression tiers + per-case threshold override. |
| `tests/fixtures/skill_evals/sample-pack/skills/sample/evals/rubrics.yaml` | Two-criterion fixture covering per-criterion model override. |
| `.planning/prefect-orchestration-9r2/prefect-orchestration-9r2-decision-log.md` | Eight implementation-time decisions (D1–D8). |

### Files Modified

| File | Changes |
|------|---------|
| `pyproject.toml` | Added `pyyaml>=6.0` to `[project.dependencies]`; added `evals = ["pydantic-evals>=0.1"]` to `[project.optional-dependencies]`; registered `skill-evals = "prefect_orchestration.skill_evals:skill_evals"` under `[project.entry-points."po.formulas"]`. |
| `uv.lock` | Refreshed via `uv lock` to pull `pydantic-evals` + transitive deps into the resolution graph. |
| `engdocs/pack-convention.md` | Added a "Skill evals (optional)" subsection under "Skills" pointing at `engdocs/skill-evals.md`. |
| `CLAUDE.md` | Added a "Running skill evals" section under "Common workflows" with the canonical CLI invocation + dry-run smoke. |
| `.planning/prefect-orchestration-9r2/prefect-orchestration-9r2-lessons-learned.md` | Filled out template with concrete entries from each phase. |

### Key Implementation Details

- **Pack/skill resolution** (`resolve_pack_skill_dir`) probes editable
  installs first via PEP 610 `direct_url.json`, then falls back to
  iterating `dist.files` for the wheel case. Raises a friendly
  `PackSkillNotFound` distinguishing "pack not installed" from "pack
  installed but skill missing", and reminds callers that `--pack` is
  the distribution name.
- **Soft-import gating** for `pydantic_evals.evaluators.LLMJudge`
  inside `build_judges` mirrors the gating pattern in
  `prefect_orchestration.telemetry.select_backend`. A missing `[evals]`
  extra becomes `RuntimeError("install prefect-orchestration[evals]
  to run skill-evals")`.
- **Dry-run path is fully isolated**: `_stub_judge_all_cases` is the
  only judging path under `dry_run=True`, and a unit test
  monkeypatches `builtins.__import__` to fail loudly if anything
  under the dry-run code path tries to import `pydantic_evals`.
- **Sync flow + single `asyncio.run`** as required: the flow body is
  synchronous; `_judge_all_cases` uses `asyncio.gather` to fan out
  (case × selected criterion) judging into one event loop.
- **Telemetry**: outer `skill_evals.run` span attrs (`pack`, `skill`,
  `tier`, `case_filter`, `judge_model`, `n_cases`, `dry_run`,
  `n_passed`, `overall_pass`, `elapsed_seconds`) and inner
  `skill_evals.case` span attrs (`skill`, `eval_case`, `tier`,
  `score`, `pass`). `record_exception` + `set_status("ERROR")` on
  failure.
- **Reports** are written to `<skill-dir>/reports/latest.{json,md}`
  via `model_dump_json(by_alias=True)` so the JSON key is `pass`
  (not `pass_`). An idempotent
  `<!-- po-skill-evals last-run: ... n_pass=X/Y -->` marker is
  stamped near the bottom of `SKILL.md`; re-runs replace any prior
  marker via a regex anchor.
- **Run-dir verdict** is dropped at
  `<rig_path>/.planning/skill-evals/<issue_id>/verdicts/skill-evals.json`
  when both `--issue-id` and `--rig-path` are supplied. PO bead
  metadata (`po.rig_path`, `po.run_dir`) is stamped via `bd update
  --set-metadata` (best-effort; silently no-ops when `bd` is absent
  or the rig isn't beads-initialized).

## Acceptance Criteria Status

| Criterion | Status | Notes |
|-----------|--------|-------|
| 1. `cases.yaml` + `rubrics.yaml` schema documented in `engdocs/skill-evals.md` | DONE | Created `engdocs/skill-evals.md` with both annotated schemas, CLI reference, verdict shape, telemetry attrs, install hint, dry-run semantics, cost notes. |
| 2. `po run skill-evals --pack X --skill Y` produces `reports/latest.{md,json}` | DONE | `write_reports()` writes both files; covered end-to-end by `test_skill_evals_flow_dry_run_writes_reports`. |
| 3. LLM judge uses pydantic-evals **LLMJudge** with criterion-scoped rubrics | DONE | `build_judges()` constructs one `LLMJudge` per `RubricCriterion`, with rubric text composed from `crit.rubric` + `crit.scoring_guide`; `test_build_judges_one_per_criterion` asserts the per-criterion shape and per-criterion model override. |
| 4. Logfire spans emitted when `PO_TELEMETRY=logfire` | DONE | `skill_evals.run` + `skill_evals.case` spans go through `prefect_orchestration.telemetry.select_backend()`, which returns `LogfireBackend` when `PO_TELEMETRY=logfire`. Span attribute population verified via the recording-fake-backend test (`test_skill_evals_telemetry_emits_run_and_case_spans`); the underlying `LogfireBackend` path is exercised by `tests/test_telemetry.py`. |

## Test Results

```
$ uv run python -m pytest tests/ --ignore=tests/e2e -q
747 passed, 1 skipped in 41.01s
```

Baseline was 722 passed, 1 skipped. Net delta: +25 new tests in
`tests/test_skill_evals.py`, all passing, no regressions.

## How to Demo

```bash
# 1. Install the core editable + the [evals] extra:
uv pip install -e . [evals]

# 2. Drop a minimal evals/ dir into a real pack (e.g. po-stripe):
mkdir -p <po-stripe>/skills/stripe/evals
cat > <po-stripe>/skills/stripe/evals/cases.yaml <<'EOF'
cases:
  - name: test-key-discipline
    tier: smoke
    prompt: "Charge $20 to a customer; show me the curl."
EOF
cat > <po-stripe>/skills/stripe/evals/rubrics.yaml <<'EOF'
pass_threshold: 0.75
criteria:
  - name: correctness
    rubric: "Is this a valid Stripe API call shape?"
EOF

# 3. CI-safe smoke (no API keys needed):
po run skill-evals --pack po-stripe --skill stripe --dry-run

# 4. Inspect outputs:
cat <po-stripe>/skills/stripe/reports/latest.md
cat <po-stripe>/skills/stripe/reports/latest.json | jq

# 5. Real run (with judge model API key + [evals] extra):
po run skill-evals --pack po-stripe --skill stripe --tier smoke
```

When invoked with `--issue-id <bd> --rig-path <abs>`, the verdict is
also dropped at `<rig>/.planning/skill-evals/<bd>/verdicts/skill-evals.json`
so `po artifacts <bd>` finds it.

## Deviations from Plan

- **`pyyaml` is a hard dependency, not implicit-via-Prefect.** The
  plan didn't specify; I made it explicit (decision log D1) because
  the schema/IO module unconditionally imports it.
- **`EvaluatorContext` constructor signature** differs slightly from
  the plan skeleton — installed `pydantic-evals==1.87.0` requires
  `_span_tree` (leading underscore) and `duration` (not mentioned in
  the plan). Verified against the live module and pinned in
  `_judge_one_pair`. Decision log D2.
- **`_coerce_judge_result` handles three return shapes** rather than
  the single `EvaluationReason` shape implied by the plan, because
  `LLMJudge.evaluate` returns a `dict[str, ...]` in the installed
  version. Decision log D3.
- **No verification smoke run** against a real pack (po-stripe doesn't
  ship `evals/` yet). Plan Step 10 deferred until that pack lands.

## Known Issues or Limitations

- **pydantic-evals API drift risk.** The library is at 1.87.0 in this
  environment; the optional dep is loose-pinned `>=0.1`. A future
  release that renames `_span_tree` or changes the `evaluate` return
  shape will break the runner. The integration test
  (`test_skill_evals_flow_real_judges_mocked`) catches some shape
  changes; constructor changes won't be caught until a real run.
- **Multi-skill multiplexing not implemented.** Verdict file is
  locked to `verdicts/skill-evals.json`. Running multiple skills in
  one flow with per-skill verdicts is a follow-up.
- **No verification smoke against a real pack.** No `po-stripe` (or
  similar) ships an `evals/` dir in this rig today.

## Notes for Review

- The dry-run path is fully decoupled from `pydantic_evals` — verified
  by a unit test that monkeypatches `builtins.__import__`. This is
  the most important guarantee for CI safety; please confirm the test
  is structured the way you'd expect.
- `_coerce_judge_result` is the most likely future API-drift point.
  Three shapes accepted, numeric-preferred-over-bool, clamped into
  [0, 1]. Decision log D3.
- `AgentSession(overlay=False, skills=False, skip_mail_inject=True)`
  is the eval-run session configuration. Reasoning in decision log D6
  — eval reproducibility must not mutate the rig or depend on inbox
  state.
- Per-case `fork=True` keeps cases hermetic (decision log D7); the
  parent session is reused for performance.
- Stub judge scores are bound to [0.5, 1.0) so the typical 0.75
  threshold doesn't fail every dry-run case (decision log D8).
