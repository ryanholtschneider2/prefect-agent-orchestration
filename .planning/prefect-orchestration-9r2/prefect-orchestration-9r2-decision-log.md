# Decision Log: prefect-orchestration-9r2

Decisions made during implementation that aren't already nailed down in
the plan. Plan-level decisions (formula in core, layout under
`<pack>/skills/<name>/evals/`, optional extra, sync flow + single
`asyncio.run`, `--dry-run` short-circuits both halves) are captured in
the plan's "Design Decisions" section and not duplicated here.

## D1 — `pyyaml` promoted to a hard dependency

**Decision.** Add `pyyaml>=6.0` to `[project.dependencies]` in core's
`pyproject.toml` rather than relying on Prefect's transitive install
or piggy-backing on the `[evals]` extra.

**Rationale.** `prefect_orchestration.skill_evals` unconditionally
imports `yaml` at module load (the `load_cases` / `load_rubrics`
helpers). Hiding that under the `[evals]` extra would cause
`from prefect_orchestration import skill_evals` to fail for anyone
using the formula plumbing without the extra (e.g. tests that
exercise `filter_cases` or schema models). Prefect ships `pyyaml`
transitively today, but transitive deps are a footgun — a future
Prefect release dropping it would silently break us. Six-line cost,
no surprise drift.

**Alternative considered.** Move all YAML I/O behind the same
`pydantic_evals` soft-import gate. Rejected because schema validation
and case filtering are useful without the judge (e.g. lint a
`cases.yaml` schema in CI before running the suite).

## D2 — `EvaluatorContext` constructor shape pinned by inspection

**Decision.** Construct `EvaluatorContext(name=..., inputs=...,
metadata=None, expected_output=None, output=..., duration=0.0,
_span_tree=None, attributes={}, metrics={})` — passing the leading-
underscore `_span_tree` positional/kwarg as the installed
`pydantic-evals==1.87.0` requires.

**Rationale.** The plan skeleton used `span_tree=None` (no leading
underscore). Empirically the dataclass field is named `_span_tree`,
and pydantic-evals accepts it as a keyword. Construction was
verified against the resolved version via
`uv run python -c "from pydantic_evals.evaluators import EvaluatorContext; ..."`.

**Risk.** Future pydantic-evals may rename or hide this field. The
`_judge_one_pair` helper is a single point of change; the integration
test (`test_skill_evals_flow_real_judges_mocked`) exercises this path
with a mocked `evaluate`, which will continue passing if the rename
happens; a separate version-bump verification step would catch
constructor-signature drift.

## D3 — `_coerce_judge_result` accepts dict, EvaluationReason, and tuple

**Decision.** The coercion helper handles three return shapes from
`LLMJudge.evaluate`:

1. `dict[str, EvaluationScalar | EvaluationReason]` — the documented
   shape in `pydantic-evals==1.87.0` (judge returns one entry per of
   `_score`/`_pass` slot it was configured for).
2. `EvaluationReason(value=..., reason=...)` — older shape some forks
   may still return.
3. `(value, reason)` 2-tuple — defensive last resort.

Numeric values are preferred over booleans when both are present in
the dict (so we get `0.83` rather than `1.0` when both are reported);
booleans become `{0.0, 1.0}` as a fallback.

**Rationale.** pydantic-evals is a young library; the source for
`LLMJudge.evaluate` returns a `dict[str, ...]` today but the README
examples show single-`EvaluationReason` returns. The plan called this
out as an API-drift risk; the coercion helper plus a unit test for
each shape is the single point of change if the API moves.

## D4 — Reports use `model_dump_json(by_alias=True)` so the key is `pass` not `pass_`

**Decision.** `CaseResult.pass_` is declared with `Field(alias="pass",
serialization_alias="pass")` and `model_config = {"populate_by_name":
True}`. Reports are written via `model_dump_json(by_alias=True)`.

**Rationale.** The verdict shape in the plan's "verdict" section uses
the bare key `pass` (Python keyword). Pydantic's standard escape is
the alias mechanism. `populate_by_name=True` lets us still construct
in Python via `CaseResult(pass=passed, ...)` — actually the runner
uses `**{"pass": passed}` because `pass` is a keyword. This keeps the
JSON shape clean for downstream tooling that doesn't know about the
trailing-underscore convention.

## D5 — `--dry-run` does not import `pydantic_evals` at all

**Decision.** Verified by a unit test
(`test_stub_judging_does_not_import_pydantic_evals`) that
monkeypatches `builtins.__import__` to assert any `pydantic_evals`
import in the dry-run path is a hard test failure. The flow
explicitly calls `_stub_judge_all_cases(...)` instead of
`build_judges` + `_judge_all_cases` when `dry_run=True`.

**Rationale.** Plan Design Decision §9 calls this out as a hard
constraint: CI must run `--dry-run` without the `[evals]` extra
installed. Using a unit test (not just code review) prevents future
refactors from accidentally re-introducing the import path under
dry-run.

## D6 — `AgentSession(overlay=False, skills=False)` for eval runs

**Decision.** The skill-evals session opts out of pack overlay/skills
materialization.

**Rationale.** Eval runs read the skill from the pack tree directly
(SKILL.md is loaded as a prompt prefix). Letting AgentSession copy
overlay/skills into the rig on first turn would (a) mutate the
caller's rig as a side-effect of running evals — surprising, and (b)
contaminate the agent's view with content from *other* installed
packs, which could bias judging. `skip_mail_inject=True` is set for
the same reason: eval reproducibility must not depend on inbox state.

## D7 — Per-case session forking (`fork=True`)

**Decision.** Each case calls `session.prompt(prompt, fork=True)` so
every case starts from a fresh fork of the session.

**Rationale.** Plan §"AgentSession driving strategy". Without forking,
case N+1 sees case N's transcript and judges score later cases
against contaminated context. The reused parent session avoids
Claude-CLI cold-start per case (~2s × N saved) while forking keeps
cases hermetic.

## D8 — Stub judge scores in [0.5, 1.0)

**Decision.** `_stub_judge_all_cases` maps `sha256(case|criterion)[:2]`
into `[0.5, 1.0)` rather than `[0.0, 1.0)`.

**Rationale.** With the default rubric `pass_threshold=0.75`, a fully
[0.0, 1.0) range would have ~75% of dry-run cases failing in CI by
default — every smoke test would need a `--pass-threshold 0.0` knob
to be useful. Scoping into [0.5, 1.0) means the typical dry-run case
passes the 0.75 threshold ~50% of the time, which is closer to what
operators expect from a smoke run, while still being able to fail
when the threshold is set high (`--pass-threshold 0.99` fails 100%).

The unit test
(`test_skill_evals_flow_pass_fail_threshold`) exercises both
directions.
