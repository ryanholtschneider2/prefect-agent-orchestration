# Lessons Learned: prefect-orchestration-9r2

## Planning Difficulties

- No significant planning friction encountered during the build phase
  itself — the plan went through one critique cycle before reaching
  the builder, and Design Decisions §1-§13 covered the layout / extra /
  async / dry-run questions cleanly.

## Implementation Difficulties

- **Issue**: `EvaluatorContext` constructor in `pydantic-evals==1.87.0`
  uses a leading-underscore field name (`_span_tree`) and a `duration`
  field not mentioned in the plan skeleton. Direct construction failed
  with `TypeError` until the field set was reflected from the live
  module.
  **Resolution**: Inspected `inspect.signature(EvaluatorContext)`
  against the resolved version, then pinned the constructor call in
  `_judge_one_pair`. Documented in decision log D2.
  **Recommendation**: Pin pydantic-evals to a specific minor version
  bound (`>=1.87,<2`) once 9r2 has soaked. The current `>=0.1` is
  optimistic and will break on the next API rev.

- **Issue**: `LLMJudge.evaluate` returns `dict[str, EvaluationScalar |
  EvaluationReason]` (via `_update_combined_output`), not the simpler
  `EvaluationReason` shape implied by the plan and the README.
  **Resolution**: `_coerce_judge_result` handles dict / object /
  tuple, prefers numeric over boolean entries, and clamps into [0,
  1]. Decision log D3.
  **Recommendation**: When the verification step runs a real `po run
  skill-evals` end-to-end, capture the exact shape of `evaluate`'s
  return for the chosen judge model so we can simplify the coercion
  if 1.87.0 is the only relevant version.

- **Issue**: `pyproject.toml` was reverted (linter or external edit)
  mid-implementation, dropping the `[evals]` extra and the
  `skill-evals` entry-point registration. Discovered when the entry-
  point unit test failed.
  **Resolution**: Re-applied the two edits and reinstalled in
  editable mode. Caught immediately because the EP test exercises
  `importlib.metadata.entry_points`.
  **Recommendation**: When EP-related tests live alongside the
  feature, keep them in the same file as the rest of the formula's
  tests so a regression surfaces at the same `pytest` invocation —
  worked correctly here.

## Testing & Verification Difficulties

- **Issue**: Reinstalling the core package via `uv pip install -e .`
  uninstalled the editable install of `po-formulas-software-dev` from
  the venv (uv does not preserve unrelated editable extras across
  `pip install -e`), causing 30+ unrelated tests to fail collection
  with `ModuleNotFoundError: No module named 'po_formulas'`.
  **Resolution**: `uv pip install -e
  /path/to/software-dev/po-formulas` after re-installing core
  restored the test suite.
  **Recommendation**: Use `uv tool install --reinstall <core>
  --with-editable <pack>` (the path PO's own pack lifecycle uses) for
  this kind of multi-pack dev setup. Or document a `make
  dev-reinstall` target in this repo that does both editable installs
  in one go.

- **Issue**: `test_skill_evals_flow_pass_fail_threshold` initially
  failed because `cases.yaml` ships `deeper-regression` with a per-case
  `pass_threshold=0.6`, which overrides the test's `--pass-threshold
  0.99`. The test was implicitly assuming the global threshold won.
  **Resolution**: Added `tier="smoke"` to filter to the case without
  the per-case override.
  **Recommendation**: When a test asserts on a flow-level knob,
  either use a freshly-built fixture (no overrides) or filter the
  fixture down to the cases the knob actually affects. Comment in
  the test now explains the filter.

## Documentation Difficulties

- No significant friction. Schema documentation in
  `engdocs/skill-evals.md` mirrors the verbatim YAML examples from the
  plan; pack-convention.md got a small pointer; CLAUDE.md got a
  workflow snippet.

## General Lessons & Follow-Ups

- **`--judge-model` flag is reachable through `po run --judge-model
  <value>`** but I did not add an explicit `judge_model` parameter
  alias in the docstring. `po run` parses any kwarg into the flow's
  signature, so `--judge-model my-model` already works. Worth a
  follow-up to add a docstring example.
- **Reports/JSON path in run_dir verdict.** The current verdict path
  is `<rig>/.planning/skill-evals/<id>/verdicts/skill-evals.json`,
  matching the convention used by other formulas. If multi-skill
  evaluation lands as a follow-up, this naming should switch to
  `verdicts/skill-evals-<skill>.json` so multiple verdicts can coexist.
- **Smoke verification not run.** The plan's Step 10 (verification
  smoke run against po-stripe) was not executed because no `po-stripe`
  pack with an `evals/` dir is shipping in this rig today. Operator
  should run the smoke once po-stripe's evals/ dir lands.
