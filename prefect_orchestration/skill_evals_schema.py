"""Pydantic models for `cases.yaml`, `rubrics.yaml`, and the verdict shape.

Kept separate from `skill_evals.py` so the flow module can stay focused
on orchestration logic. These models double as the JSON schema for
`reports/latest.json` and `verdicts/skill-evals.json`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Tier = Literal["smoke", "regression", "full"]


class CaseSpec(BaseModel):
    """One evaluation case for a skill."""

    name: str
    prompt: str
    tier: Tier = "smoke"
    # When None / empty, the runner uses every criterion from rubrics.yaml.
    evaluators: list[str] | None = None
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)
    # Optional case-level override; falls back to rubrics-file pass_threshold.
    pass_threshold: float | None = None


class CasesFile(BaseModel):
    cases: list[CaseSpec]


class RubricCriterion(BaseModel):
    """One judging criterion. Becomes one `LLMJudge` instance at run time."""

    name: str
    rubric: str
    scoring_guide: str | None = None
    # Optional per-criterion judge override (e.g. cheap model for binary safety).
    model: str | None = None
    include_input: bool = True


class RubricsFile(BaseModel):
    judge_model: str | None = None
    pass_threshold: float = 0.75
    criteria: list[RubricCriterion]


class CriterionResult(BaseModel):
    criterion: str
    score: float  # 0.0 - 1.0
    reason: str | None = None


class CaseResult(BaseModel):
    case: str
    tier: str
    score: float  # mean across criteria evaluated for this case
    # `pass` is a Python keyword; serialize with the friendly key.
    pass_: bool = Field(alias="pass", serialization_alias="pass")
    criteria: list[CriterionResult]
    output: str  # truncated agent output
    elapsed_seconds: float

    model_config = {"populate_by_name": True}


class SkillEvalsVerdict(BaseModel):
    """Identical shape for `reports/latest.json` and `verdicts/skill-evals.json`."""

    skill: str
    pack: str
    judge_model: str | None
    tier: str | None
    case_filter: str | None
    pass_threshold: float
    n_cases: int
    n_passed: int
    overall_pass: bool
    cases: list[CaseResult]
    started_at: str  # ISO-8601 UTC
    finished_at: str
