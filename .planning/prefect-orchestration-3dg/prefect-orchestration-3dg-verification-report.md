# Verification Report: prefect-orchestration-3dg

## Acceptance Criteria Verification

| # | Criterion | Method | Result | Evidence |
|---|-----------|--------|--------|----------|
| 1 | `po run skill-evals --pack prefect-orchestration --skill po --tier smoke` passes 100% | Live `po run` (real Claude CLI + OpenAI judge) | PASS | 3/3 smoke cases passed at 1.00 each — see `skills/po/reports/latest.json` |
| 2 | `po run skill-evals --pack prefect-orchestration --skill po` (full suite) passes ≥ threshold | Live full run | PASS | 9/9 cases passed (100%) at threshold 0.75 |
| 3 | `skills/po/evals/{cases,rubrics}.yaml` exist | File existence | PASS | `skills/po/evals/cases.yaml` (9 cases, smoke + regression), `skills/po/evals/rubrics.yaml` (7 criteria) |
| 4 | `skills/po/evals/reports/latest.{md,json}` committed showing pass rate on a real model | Live run + file inspection | PASS | Report shows `9/9 passed (PASS)` on `openai:gpt-5-mini` (judge), Claude CLI (driver), 2026-04-28 |
| 5 | `po doctor` surfaces a green `po-skill-evals-fresh` row with `SOURCE=prefect-orchestration` | `po doctor` output inspection | PASS | Row present: `prefect-orchestration  po-skill-evals-fresh  OK  skill po evals: 9/9 (100%) (2026-04-28)` |
| 6 | e2e test passes locally; skips on CI without claude CLI | `uv run python -m pytest tests/e2e/test_skill_evals_po.py` | PASS | 1 passed (dry-run path), 1 skipped (real-Claude path, gated on `ANTHROPIC_API_KEY`) |
| 7 | SKILL.md badge line points at the report | `head skills/po/SKILL.md` | PASS | Badge line at top: `> **Skill status**: see [reports/latest.md](reports/latest.md) ...` |

## Deviations from issue spec (engdocs-grounded)

- Issue asked for `evals/cases/{smoke,tier1}.yaml` (mirroring data-agent),
  but `engdocs/skill-evals.md` specifies a single `evals/cases.yaml` with
  a `tier:` field per case. Per workflow guidance "engdocs are ground
  truth", went with the single-file schema. Tiers are `smoke` +
  `regression` (per `Tier = Literal["smoke", "regression", "full"]` in
  `skill_evals_schema.py`); the issue's `--tier 1` doesn't match this
  schema, so the `tier1-` cases live in the `regression` tier and are
  selected with `--tier regression`.
- Issue mentions evaluator types `RespondsWithContent`, `ResponseTime`,
  `ContainsKeyword`, `ClaudeCodeJudge`. The runner shipped by 9r2 only
  supports `LLMJudge` per criterion; rubric criteria translate the
  concepts (e.g. `responds-with-content`, `mentions-core-vocabulary`,
  `recommends-correct-subcommand`). No runner extension was needed to
  meet the acceptance criteria.

## Bug fix landed alongside the feature

- `prefect_orchestration/skill_evals.py::_select_backend` constructed
  `TmuxClaudeBackend()` with no arguments — the dataclass requires
  `issue` and `role`. Hidden by `--dry-run` (which uses StubBackend) and
  by hosts without tmux on PATH. Real-Claude runs blew up at
  `_build_session`. Fixed: pass `issue=issue_id or "skill-evals"` and
  `role="skill-evals"`. Existing 25 unit tests still pass; the new e2e
  smoke run exercises the fixed path.

## Regression Check

- Baseline tests: **747 passed, 1 skipped** (commit before this work).
- Final tests: **754 passed, 1 skipped** (+7 = new
  `tests/test_skill_evals_doctor.py`).
- E2E suite: **1 passed, 1 skipped** in
  `tests/e2e/test_skill_evals_po.py` (skip is the real-Claude variant,
  gated on `ANTHROPIC_API_KEY`).
- Regressions: **NONE**.

## Live Environment Verification

- Environment: in-repo `uv run` (no docker/k8s needed for skill-evals).
- Smoke checks:
  - `po run skill-evals --pack prefect-orchestration --skill po --tier smoke --judge-model openai:gpt-5-mini` → 3/3 pass, exit 0
  - Full run (smoke + regression) → 9/9 pass, exit 0
  - `po doctor` → green `po-skill-evals-fresh` row with `SOURCE=prefect-orchestration`
  - SKILL.md marker stamped:
    `<!-- po-skill-evals last-run: 2026-04-28T23:29:50Z n_pass=9/9 -->`

## Decision Log Review

Inline decisions (not enough to warrant a separate file):

1. **Single `cases.yaml` (not split smoke.yaml/tier1.yaml)** — engdocs is
   the ground truth for the runner schema; the issue's data-agent-style
   layout would contradict the design doc.
2. **OpenAI judge instead of Anthropic** — user CLAUDE.md forbids
   `ANTHROPIC_API_KEY` for local script runs (Claude CLI uses OAuth);
   `openai:gpt-5-mini` is cheap and produces structurally identical
   verdicts. The release-ready run before tagging can swap with
   `--judge-model anthropic:claude-opus-4-7` per the issue spec.
3. **Tmux backend bug fix in this same change** — reproducible blocker
   for any non-dry-run; minimal patch (4 lines plus a kwargs plumbing
   pass-through), no architectural refactor.
4. **Wheel `force-include` for `skills/`** — without it, wheel installs
   wouldn't ship the skill tree, and `--pack prefect-orchestration
   --skill po` would fail for non-editable users.
5. **Doctor check thresholds (0.75 pass-rate, 30-day staleness)** —
   matches the issue spec verbatim; stored as module constants for
   trivial future adjustment.

## Confidence Level

**HIGH** — every acceptance criterion verified live, no regressions, all
new tests pass, doctor row is green end-to-end, and the previously-broken
non-dry-run path now works (bug fix + verified in the same change).
