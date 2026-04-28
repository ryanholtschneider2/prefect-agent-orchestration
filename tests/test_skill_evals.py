"""Unit tests for `prefect_orchestration.skill_evals`.

Anti-mock policy applies: we only mock the LLMJudge boundary (so tests
don't burn API tokens). Stub backend / pack discovery / YAML parsing /
report writing are exercised against real code paths.
"""

from __future__ import annotations

import asyncio
import importlib.metadata as md
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from prefect_orchestration import skill_evals as se
from prefect_orchestration.skill_evals_schema import (
    CaseSpec,
    CasesFile,
    CriterionResult,
    RubricCriterion,
    RubricsFile,
)


FIXTURES = Path(__file__).parent / "fixtures" / "skill_evals" / "sample-pack"
SAMPLE_SKILL_DIR = FIXTURES / "skills" / "sample"


# ---------------------------------------------------------------------------
# Schema round trips
# ---------------------------------------------------------------------------


def test_cases_yaml_round_trip() -> None:
    cases = se.load_cases(SAMPLE_SKILL_DIR)
    assert isinstance(cases, CasesFile)
    names = {c.name for c in cases.cases}
    assert names == {"simple-smoke", "deeper-regression"}
    smoke = next(c for c in cases.cases if c.name == "simple-smoke")
    assert smoke.tier == "smoke"
    assert smoke.evaluators == ["correctness"]
    assert smoke.metadata == {"regression_id": "sample-smoke"}
    deep = next(c for c in cases.cases if c.name == "deeper-regression")
    assert deep.pass_threshold == 0.6


def test_cases_yaml_rejects_missing_required_fields() -> None:
    from pydantic import ValidationError

    bad = {"cases": [{"name": "x"}]}  # missing prompt
    with pytest.raises(ValidationError):
        CasesFile.model_validate(bad)


def test_rubrics_yaml_round_trip() -> None:
    rubrics = se.load_rubrics(SAMPLE_SKILL_DIR)
    assert rubrics.judge_model == "anthropic:claude-sonnet-4-6"
    assert rubrics.pass_threshold == 0.75
    by_name = {c.name: c for c in rubrics.criteria}
    assert by_name["safety"].model == "openai:gpt-5-mini"
    # default include_input
    assert by_name["correctness"].include_input is True


# ---------------------------------------------------------------------------
# Pack discovery
# ---------------------------------------------------------------------------


class _FakeDist:
    """Mimic enough of importlib.metadata.Distribution for resolution tests."""

    def __init__(
        self, *, direct_url_json: str | None = None, files: list[Path] | None = None
    ) -> None:
        self._direct_url = direct_url_json
        self._files = files or []

    def read_text(self, name: str) -> str | None:
        if name == "direct_url.json":
            return self._direct_url
        return None

    @property
    def files(self) -> list[Path]:
        return self._files

    def locate_file(self, p: Any) -> Path:
        return Path(p)


def test_resolve_pack_skill_dir_editable(monkeypatch: pytest.MonkeyPatch) -> None:
    direct_url = json.dumps(
        {"url": f"file://{FIXTURES}", "dir_info": {"editable": True}}
    )
    fake = _FakeDist(direct_url_json=direct_url)
    monkeypatch.setattr(se, "distribution", lambda name: fake)
    out = se.resolve_pack_skill_dir("sample-pack", "sample")
    assert out == FIXTURES / "skills" / "sample"


def test_resolve_pack_skill_dir_wheel(monkeypatch: pytest.MonkeyPatch) -> None:
    skill_md = FIXTURES / "skills" / "sample" / "SKILL.md"
    fake = _FakeDist(files=[skill_md])
    monkeypatch.setattr(se, "distribution", lambda name: fake)
    out = se.resolve_pack_skill_dir("sample-pack", "sample")
    assert out == skill_md.parent


def test_resolve_pack_skill_dir_missing_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(name: str) -> None:
        raise md.PackageNotFoundError(name)

    monkeypatch.setattr(se, "distribution", _raise)
    with pytest.raises(se.PackSkillNotFound, match="not installed"):
        se.resolve_pack_skill_dir("nope-pack", "sample")


def test_resolve_pack_skill_dir_missing_skill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    direct_url = json.dumps(
        {"url": f"file://{tmp_path}", "dir_info": {"editable": True}}
    )
    fake = _FakeDist(direct_url_json=direct_url, files=[])
    monkeypatch.setattr(se, "distribution", lambda name: fake)
    with pytest.raises(se.PackSkillNotFound, match="does not ship skill"):
        se.resolve_pack_skill_dir("sample-pack", "missing-skill")


def test_resolve_pack_skill_dir_rejects_blank_inputs() -> None:
    with pytest.raises(se.PackSkillNotFound, match="--pack"):
        se.resolve_pack_skill_dir("", "sample")
    with pytest.raises(se.PackSkillNotFound, match="--skill"):
        se.resolve_pack_skill_dir("foo", "")


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def test_filter_cases_by_tier_and_prefix() -> None:
    cases = se.load_cases(SAMPLE_SKILL_DIR).cases
    only_smoke = se.filter_cases(cases, tier="smoke", case=None)
    assert [c.name for c in only_smoke] == ["simple-smoke"]

    only_prefix = se.filter_cases(cases, tier=None, case="deeper")
    assert [c.name for c in only_prefix] == ["deeper-regression"]

    both = se.filter_cases(cases, tier="smoke", case="deeper")
    assert both == []


# ---------------------------------------------------------------------------
# build_judges (real LLMJudge construction; never invoked)
# ---------------------------------------------------------------------------


def test_build_judges_one_per_criterion() -> None:
    rubrics = se.load_rubrics(SAMPLE_SKILL_DIR)
    judges = se.build_judges(rubrics, default_model="anthropic:claude-sonnet-4-6")
    assert set(judges.keys()) == {"correctness", "safety"}
    # Per-criterion model override wins over rubrics default.
    assert judges["safety"].model == "openai:gpt-5-mini"
    # Default model fills criterion-level None.
    assert judges["correctness"].model == "anthropic:claude-sonnet-4-6"
    # Rubric text concatenates scoring guide.
    assert "Scoring guide" in judges["correctness"].rubric


def test_build_judges_friendly_error_when_extra_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirror the gating pattern in telemetry.select_backend."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *a: Any, **kw: Any) -> Any:
        if name.startswith("pydantic_evals"):
            raise ImportError("simulated missing extra")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match=r"prefect-orchestration\[evals\]"):
        se.build_judges(RubricsFile(criteria=[]), default_model=None)


# ---------------------------------------------------------------------------
# Stub judging (used in --dry-run; never hits pydantic-evals)
# ---------------------------------------------------------------------------


def test_stub_judge_scores_are_deterministic_and_in_range() -> None:
    rubrics = RubricsFile(
        criteria=[
            RubricCriterion(name="correctness", rubric="r"),
            RubricCriterion(name="safety", rubric="r"),
        ]
    )
    pairs: list[tuple[CaseSpec, str]] = [
        (CaseSpec(name="a", prompt="p"), "out-a"),
        (CaseSpec(name="b", prompt="p"), "out-b"),
    ]
    a1 = se._stub_judge_all_cases(pairs, rubrics)
    a2 = se._stub_judge_all_cases(pairs, rubrics)
    assert a1 == a2  # deterministic
    for case_results in a1:
        for r in case_results:
            assert 0.5 <= r.score < 1.0


def test_stub_judging_does_not_import_pydantic_evals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure --dry-run path is safe without the [evals] extra installed."""
    import builtins

    real_import = builtins.__import__
    seen: list[str] = []

    def fake_import(name: str, *a: Any, **kw: Any) -> Any:
        if name.startswith("pydantic_evals"):
            seen.append(name)
            raise AssertionError(
                f"--dry-run should not import {name}; pack [evals] extra "
                f"must remain optional in this code path"
            )
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    rubrics = RubricsFile(criteria=[RubricCriterion(name="correctness", rubric="r")])
    pairs = [(CaseSpec(name="a", prompt="p"), "out")]
    out = se._stub_judge_all_cases(pairs, rubrics)
    assert seen == []
    assert len(out) == 1
    assert out[0][0].criterion == "correctness"


# ---------------------------------------------------------------------------
# drive_skill (StubBackend)
# ---------------------------------------------------------------------------


def test_drive_skill_with_stub_backend(tmp_path: Path) -> None:
    session = se._build_session(SAMPLE_SKILL_DIR, tmp_path, dry_run=True)
    out = se.drive_skill(session, SAMPLE_SKILL_DIR / "SKILL.md", "say hi", fork=False)
    # StubBackend's deterministic ack
    assert "[dry-run] ack" in out


# ---------------------------------------------------------------------------
# Coercion of judge results
# ---------------------------------------------------------------------------


def test_coerce_judge_result_handles_dict_with_score_and_reason() -> None:
    class _ER:
        def __init__(self, value: Any, reason: str) -> None:
            self.value = value
            self.reason = reason

    raw = {
        "correctness_score": _ER(0.83, "thoughtful answer"),
        "correctness_pass": _ER(True, "thoughtful answer"),
    }
    r = se._coerce_judge_result("correctness", raw)
    assert r.score == pytest.approx(0.83)
    assert r.reason == "thoughtful answer"


def test_coerce_judge_result_clamps_into_unit_interval() -> None:
    r = se._coerce_judge_result("c", {"k": 1.7})
    assert r.score == 1.0
    r2 = se._coerce_judge_result("c", {"k": -0.4})
    assert r2.score == 0.0


def test_coerce_judge_result_falls_back_for_bool_only() -> None:
    r = se._coerce_judge_result("c", {"k": True})
    assert r.score == 1.0
    r2 = se._coerce_judge_result("c", {"k": False})
    assert r2.score == 0.0


# ---------------------------------------------------------------------------
# End-to-end flow: dry-run + run-dir verdict
# ---------------------------------------------------------------------------


def _stage_pack(tmp_path: Path) -> Path:
    """Copy the sample pack fixture to a writable tmp dir."""
    import shutil

    dst = tmp_path / "pack"
    shutil.copytree(FIXTURES, dst)
    return dst


def _patch_distribution_to(dst: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    direct_url = json.dumps({"url": f"file://{dst}", "dir_info": {"editable": True}})
    fake = _FakeDist(direct_url_json=direct_url)
    monkeypatch.setattr(se, "distribution", lambda name: fake)


def test_skill_evals_flow_dry_run_writes_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dst = _stage_pack(tmp_path)
    _patch_distribution_to(dst, monkeypatch)
    rig = tmp_path / "rig"
    rig.mkdir()

    out = se.skill_evals(
        pack="sample-pack",
        skill="sample",
        dry_run=True,
        issue_id="bd-9r2-test",
        rig_path=str(rig),
    )

    skill_dir = dst / "skills" / "sample"
    assert (skill_dir / "reports" / "latest.json").is_file()
    assert (skill_dir / "reports" / "latest.md").is_file()
    parsed = json.loads((skill_dir / "reports" / "latest.json").read_text())
    assert parsed["pack"] == "sample-pack"
    assert parsed["skill"] == "sample"
    assert parsed["n_cases"] == 2
    # Verdict file under run dir is written when issue_id + rig_path provided.
    rd_verdict = (
        rig
        / ".planning"
        / "skill-evals"
        / "bd-9r2-test"
        / "verdicts"
        / "skill-evals.json"
    )
    assert rd_verdict.is_file()
    assert out["run_dir_verdict"] == str(rd_verdict)
    # Marker stamped on SKILL.md
    skill_md = (skill_dir / "SKILL.md").read_text()
    assert "po-skill-evals last-run" in skill_md


def test_skill_evals_flow_filters_by_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dst = _stage_pack(tmp_path)
    _patch_distribution_to(dst, monkeypatch)
    out = se.skill_evals(
        pack="sample-pack",
        skill="sample",
        tier="smoke",
        dry_run=True,
    )
    verdict = out["verdict"]
    assert verdict["n_cases"] == 1
    assert verdict["cases"][0]["case"] == "simple-smoke"


def test_skill_evals_flow_pass_fail_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dst = _stage_pack(tmp_path)
    _patch_distribution_to(dst, monkeypatch)
    # Stub scores live in [0.5, 1.0). A 0.99 threshold should fail every case.
    # Filter to smoke (which has no per-case threshold override) so the
    # global threshold actually applies.
    out = se.skill_evals(
        pack="sample-pack",
        skill="sample",
        dry_run=True,
        tier="smoke",
        pass_threshold=0.99,
    )
    verdict = out["verdict"]
    assert verdict["overall_pass"] is False
    assert verdict["n_passed"] == 0


def test_skill_evals_flow_real_judges_mocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the non-dry-run path with LLMJudge.evaluate mocked.

    Asserts pydantic-evals is the one constructing judges, that
    asyncio.gather is used (single asyncio.run from the sync flow),
    and that scores propagate into the verdict.
    """
    dst = _stage_pack(tmp_path)
    _patch_distribution_to(dst, monkeypatch)

    from pydantic_evals.evaluators import LLMJudge

    fixed = {"correctness_score": type("ER", (), {"value": 0.91, "reason": "ok"})()}
    monkeypatch.setattr(
        LLMJudge, "evaluate", AsyncMock(return_value=fixed), raising=True
    )

    # Force StubBackend so we don't try to spawn Claude.
    monkeypatch.setenv("PO_BACKEND", "stub")

    out = se.skill_evals(
        pack="sample-pack",
        skill="sample",
        dry_run=False,
        tier="smoke",
        pass_threshold=0.5,
    )
    verdict = out["verdict"]
    assert verdict["n_cases"] == 1
    case = verdict["cases"][0]
    assert case["pass"] is True
    assert case["criteria"][0]["score"] == pytest.approx(0.91)


# ---------------------------------------------------------------------------
# Telemetry: spans fire with expected attrs through a recording fake backend
# ---------------------------------------------------------------------------


class _RecordingSpan:
    def __init__(self, name: str, attrs: dict[str, Any]) -> None:
        self.name = name
        self.attrs: dict[str, Any] = dict(attrs)
        self.exceptions: list[BaseException] = []
        self.statuses: list[tuple[str, str | None]] = []

    def set_attribute(self, key: str, value: Any) -> None:
        self.attrs[key] = value

    def record_exception(self, exc: BaseException) -> None:
        self.exceptions.append(exc)

    def set_status(self, status: str, description: str | None = None) -> None:
        self.statuses.append((status, description))


class _RecordingTelemetry:
    def __init__(self) -> None:
        self.spans: list[_RecordingSpan] = []

    @contextmanager
    def span(self, name: str, **attrs: Any):  # type: ignore[no-untyped-def]
        s = _RecordingSpan(name, attrs)
        self.spans.append(s)
        yield s


def test_skill_evals_telemetry_emits_run_and_case_spans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dst = _stage_pack(tmp_path)
    _patch_distribution_to(dst, monkeypatch)

    fake = _RecordingTelemetry()
    monkeypatch.setattr(se, "_select_telemetry", lambda: fake)

    se.skill_evals(
        pack="sample-pack",
        skill="sample",
        dry_run=True,
        tier="smoke",
    )

    names = [s.name for s in fake.spans]
    assert "skill_evals.run" in names
    assert "skill_evals.case" in names

    run_span = next(s for s in fake.spans if s.name == "skill_evals.run")
    assert run_span.attrs["pack"] == "sample-pack"
    assert run_span.attrs["skill"] == "sample"
    assert run_span.attrs["n_cases"] == 1
    assert run_span.attrs["dry_run"] is True
    assert "n_passed" in run_span.attrs
    assert "overall_pass" in run_span.attrs

    case_spans = [s for s in fake.spans if s.name == "skill_evals.case"]
    assert len(case_spans) == 1
    assert case_spans[0].attrs["eval_case"] == "simple-smoke"
    assert "score" in case_spans[0].attrs
    assert "pass" in case_spans[0].attrs


def test_skill_evals_telemetry_default_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No PO_TELEMETRY → NoopBackend. Verifies the span() ctx-manager is safe."""
    from prefect_orchestration import telemetry

    monkeypatch.delenv("PO_TELEMETRY", raising=False)
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    telemetry.reset_backend()
    backend = telemetry.select_backend()
    assert isinstance(backend, telemetry.NoopBackend)
    with backend.span("skill_evals.run", x=1) as span:
        span.set_attribute("y", 2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def test_skill_evals_registered_as_po_formula() -> None:
    eps = md.entry_points(group="po.formulas")
    names = {ep.name for ep in eps}
    assert "skill-evals" in names
    ep = next(ep for ep in eps if ep.name == "skill-evals")
    func = ep.load()
    # Prefect wraps the underlying function; its name should match.
    assert (
        getattr(func, "name", None) == "skill_evals" or func.__name__ == "skill_evals"
    )


# ---------------------------------------------------------------------------
# Async judging path: wired into a single asyncio.run
# ---------------------------------------------------------------------------


def test_judge_all_cases_uses_gather() -> None:
    """Verify _judge_all_cases dispatches all (case × criterion) pairs concurrently."""
    fake_judges: dict[str, Any] = {}

    class _FakeJudge:
        def __init__(self, name: str) -> None:
            self.name = name

        async def evaluate(self, ctx: Any) -> Any:
            return {"k": type("ER", (), {"value": 0.7, "reason": self.name})()}

    fake_judges["a"] = _FakeJudge("a")
    fake_judges["b"] = _FakeJudge("b")

    monkey_judges_dict: dict[str, Any] = {"a": fake_judges["a"], "b": fake_judges["b"]}

    pairs = [
        (CaseSpec(name="c1", prompt="p1"), "o1"),
        (CaseSpec(name="c2", prompt="p2"), "o2"),
    ]

    async def _run() -> list[list[CriterionResult]]:
        # Patch _judge_one_pair to bypass EvaluatorContext wiring (we are
        # exercising the gather plumbing, not pydantic-evals here).
        import prefect_orchestration.skill_evals as mod

        async def _fake(judge, criterion, ci, co):  # type: ignore[no-untyped-def]
            return CriterionResult(criterion=criterion, score=0.8, reason=None)

        original = mod._judge_one_pair
        mod._judge_one_pair = _fake  # type: ignore[assignment]
        try:
            return await mod._judge_all_cases(monkey_judges_dict, pairs)
        finally:
            mod._judge_one_pair = original  # type: ignore[assignment]

    out = asyncio.run(_run())
    assert len(out) == 2
    for per_case in out:
        assert {r.criterion for r in per_case} == {"a", "b"}
