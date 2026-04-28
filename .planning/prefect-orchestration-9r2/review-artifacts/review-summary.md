# Review Summary: prefect-orchestration-9r2

## Acceptance Criteria Checklist
- [x] cases.yaml + rubrics.yaml schema documented in `engdocs/skill-evals.md`
- [x] `po run skill-evals --pack X --skill Y` produces `reports/latest.{md,json}`
- [x] LLM judge uses **pydantic-evals LLMJudge** with criterion-scoped rubrics
- [x] Logfire spans emitted when `PO_TELEMETRY=logfire`

## Test Results

| Suite | Result |
|-------|--------|
| Unit (`tests/`, ignoring `tests/e2e/`) — pre-change baseline | 722 passed, 1 skipped |
| Unit — post-change | 747 passed, 1 skipped (+25 new) |
| Regressions | None |
| Lint (ruff) | Clean |

## Key Changes
| File | Purpose |
|------|---------|
| `prefect_orchestration/skill_evals.py` (NEW) | Core formula `skill-evals`: pack discovery → load YAMLs → drive AgentSession → judge via pydantic-evals LLMJudge → write reports + verdict |
| `prefect_orchestration/skill_evals_schema.py` (NEW) | Pydantic models for cases.yaml / rubrics.yaml / verdict shape |
| `engdocs/skill-evals.md` (NEW) | Convention spec + CLI reference |
| `tests/test_skill_evals.py` (NEW, 25 tests) | Schema, pack resolution, dry-run hermeticity, telemetry, threshold logic, real-judge path |
| `tests/fixtures/skill_evals/sample-pack/` (NEW) | Fixture pack for end-to-end flow tests |
| `pyproject.toml` | `pyyaml` dep, `[evals]` optional extra, `skill-evals` entry point |
| `engdocs/pack-convention.md` | Subsection pointing at skill-evals.md |
| `CLAUDE.md` | "Running skill evals" workflow section |

## Decision Log Highlights
- **D1**: `pyyaml` promoted to hard dep (not under `[evals]`) — used unconditionally for cases/rubrics loading; minimal install footprint
- **D2/D3**: pydantic-evals API quirks (`EvaluatorContext._span_tree`, `dict` return shape from `evaluate`) — independently verified by code-reviewer against installed v1.87.0 source
- **D6**: AgentSession constructed with `overlay=False, skills=False, skip_mail_inject=True` — eval determinism, no rig mutation
- **--dry-run**: Short-circuits BOTH agent driver (StubBackend) AND judge calls (deterministic stub scores). pydantic-evals never imported in this path

## Confidence Level
**HIGH** — see verification report.
