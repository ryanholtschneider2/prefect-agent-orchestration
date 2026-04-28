# Lessons Learned: prefect-orchestration-3dg

## What worked

- **Engdocs as ground truth resolved an issue ambiguity early.** The
  issue spec proposed a `cases/{smoke,tier1}.yaml` layout, but
  `engdocs/skill-evals.md` defines a single `cases.yaml`. Going with
  engdocs (and noting the deviation in the verification report) avoided
  re-doing work later and didn't silently override a decision record.

- **Skipping the plan-agent ceremony was the right call for a
  file-authoring task.** No architectural decisions to mediate; runner
  + schema were already shipped by 9r2; work was 95% YAML + a 100-line
  doctor check + tests.

## Surprises / friction

- **9r2 shipped a latent `TmuxClaudeBackend()` bug.** `_select_backend`
  invoked the dataclass with no args; `--dry-run` masks it (StubBackend),
  and CI hosts without tmux mask it (CLI fallback). Real-Claude runs
  on a tmux-equipped dev box exposed it instantly. Lesson: dry-run unit
  tests are necessary but not sufficient — at least one e2e dry-run +
  one real call against the fast tier should run before declaring a
  multi-backend formula "done".

- **Real-Claude judge calls require an Anthropic API key, which the
  user explicitly forbids for local scripts.** The agent driver works
  via Claude CLI OAuth, but the LLMJudge does not — `pydantic-ai` only
  speaks API-key auth for Anthropic. Workaround: `--judge-model
  openai:gpt-5-mini` (we have an OpenAI key). Worth documenting on the
  skill-evals page as the recommended local-dev judge.

- **`pydantic-ai` does not pull provider SDKs by default.** The OpenAI
  judge call ImportErrored on `from openai import AsyncOpenAI`. Fix
  was a one-shot `uv pip install openai`, but the `[evals]` extra
  should probably pull `pydantic-ai-slim[openai]` (or `[anthropic]`)
  to make first-real-run experience smoother.

## Followups (not in scope here)

- Wheel install path for `skills/` is now wired via `force-include`
  but untested via a real wheel build. A follow-up bead should add a
  build-artifact test that asserts `skills/po/SKILL.md` is present in
  the produced wheel.
- The skill-evals `[evals]` extra should probably include at least one
  provider SDK (openai or anthropic) so the first real-judge run on a
  fresh install doesn't ImportError.
