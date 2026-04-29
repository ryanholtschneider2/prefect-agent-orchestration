"""po formula: `skill-evals` — run a pack-shipped skill against its eval suite.

Resolves a skill folder via `--pack <distribution-name> --skill <name>`, loads
`evals/cases.yaml` + `evals/rubrics.yaml` from that folder, drives each case
through an `AgentSession`, then judges every (case × criterion) pair with
`pydantic_evals.evaluators.LLMJudge`. Writes machine + human reports next to
the skill (`reports/latest.{json,md}`) plus a verdict file under the run dir
when invoked with bead context.

`pydantic-evals` is an **optional** dependency (the `[evals]` extra). When
absent, the formula raises a friendly RuntimeError pointing at the install
command. `--dry-run` short-circuits both the agent driver and the judge
calls; in that mode pydantic-evals is not imported at all, so CI / smoke
tests can run without the extra installed and without API keys set.

See `engdocs/skill-evals.md` for schema + telemetry details.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from prefect import flow, get_run_logger

from prefect_orchestration.agent_session import (
    AgentSession,
    ClaudeCliBackend,
    StubBackend,
    TmuxClaudeBackend,
)
from prefect_orchestration.skill_evals_schema import (
    CaseResult,
    CaseSpec,
    CasesFile,
    CriterionResult,
    RubricCriterion,
    RubricsFile,
    SkillEvalsVerdict,
)
from prefect_orchestration.telemetry import select_backend as _select_telemetry

if TYPE_CHECKING:  # pragma: no cover
    from pydantic_evals.evaluators import LLMJudge


_OUTPUT_TRUNCATE_BYTES = 2048
_SKILL_MARKER_RE = re.compile(
    r"^<!--\s*po-skill-evals last-run:.*?-->\s*$", re.MULTILINE
)


class PackSkillNotFound(RuntimeError):
    """Raised when `--pack X --skill Y` cannot be resolved on disk."""


# ---------------------------------------------------------------------------
# Pack / skill resolution
# ---------------------------------------------------------------------------


def resolve_pack_skill_dir(pack: str, skill: str) -> Path:
    """Return the absolute path to `<pack-dist-root>/skills/<skill>/`.

    Resolution order:
      1. Editable install — read PEP 610 `direct_url.json`, take the
         `file://` URL, append `skills/<skill>/`.
      2. Wheel install — iterate `dist.files` for any path under
         `skills/<skill>/SKILL.md`; resolve via `dist.locate_file()`.

    Raises `PackSkillNotFound` with a fixable message when the pack
    isn't installed or doesn't ship that skill.
    """
    if not pack:
        raise PackSkillNotFound("--pack must be a distribution name (e.g. 'po-stripe')")
    if not skill:
        raise PackSkillNotFound("--skill must be a non-empty skill name")

    try:
        dist = distribution(pack)
    except PackageNotFoundError as e:
        raise PackSkillNotFound(
            f"pack {pack!r} not installed; run `po packs install {pack}`. "
            f"(--pack is the distribution name, e.g. 'po-stripe', not the "
            f"importable module 'po_stripe'.)"
        ) from e

    attempted: list[str] = []

    # 1) Editable install — direct_url.json with file:// URL.
    raw = None
    try:
        raw = dist.read_text("direct_url.json")
    except Exception:
        raw = None
    if raw:
        try:
            data = json.loads(raw)
        except ValueError:
            data = {}
        url = data.get("url", "") if isinstance(data, dict) else ""
        if url.startswith("file://"):
            base = Path(url[len("file://") :])
            candidate = base / "skills" / skill
            attempted.append(str(candidate))
            if candidate.is_dir() and (candidate / "SKILL.md").is_file():
                return candidate

    # 2) Wheel install — dist.files holds RECORD entries.
    files = dist.files or []
    target_suffix = f"skills/{skill}/SKILL.md"
    for rec in files:
        # `rec` is a `PackagePath`; comparing as POSIX is portable.
        if rec.as_posix().endswith(target_suffix):
            try:
                located = Path(dist.locate_file(rec))
            except Exception:
                continue
            attempted.append(str(located.parent))
            if located.is_file():
                return located.parent

    raise PackSkillNotFound(
        f"pack {pack!r} is installed but does not ship skill {skill!r}. "
        f"Expected SKILL.md under skills/{skill}/. "
        f"Attempted paths: {attempted or '(none)'}"
    )


# ---------------------------------------------------------------------------
# YAML IO
# ---------------------------------------------------------------------------


def load_cases(skill_dir: Path) -> CasesFile:
    path = skill_dir / "evals" / "cases.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"missing cases.yaml at {path}")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return CasesFile.model_validate(raw)


def load_rubrics(skill_dir: Path) -> RubricsFile:
    path = skill_dir / "evals" / "rubrics.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"missing rubrics.yaml at {path}")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return RubricsFile.model_validate(raw)


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def filter_cases(
    cases: list[CaseSpec],
    *,
    tier: str | None,
    case: str | None,
) -> list[CaseSpec]:
    """Apply --tier and --case (prefix) filters preserving input order."""
    out = list(cases)
    if tier:
        out = [c for c in out if c.tier == tier]
    if case:
        out = [c for c in out if c.name.startswith(case)]
    return out


# ---------------------------------------------------------------------------
# Judge construction
# ---------------------------------------------------------------------------


def build_judges(
    rubrics: RubricsFile, default_model: str | None
) -> dict[str, LLMJudge]:
    """One `LLMJudge` per criterion; rubric text appends scoring guide.

    Raises `RuntimeError` with the install hint if `pydantic-evals` is
    not importable. Mirrors the gating pattern in
    `prefect_orchestration.telemetry.select_backend`.
    """
    try:
        from pydantic_evals.evaluators import LLMJudge
    except ImportError as e:
        raise RuntimeError(
            "install prefect-orchestration[evals] to run skill-evals "
            "(`pip install 'prefect-orchestration[evals]'`)"
        ) from e

    out: dict[str, LLMJudge] = {}
    for crit in rubrics.criteria:
        rubric_text = _compose_rubric_text(crit)
        kwargs: dict[str, Any] = {
            "rubric": rubric_text,
            "include_input": crit.include_input,
        }
        chosen = crit.model or default_model
        if chosen is not None:
            kwargs["model"] = chosen
        out[crit.name] = LLMJudge(**kwargs)
    return out


def _compose_rubric_text(crit: RubricCriterion) -> str:
    if crit.scoring_guide:
        return f"{crit.rubric}\n\nScoring guide:\n{crit.scoring_guide}"
    return crit.rubric


# ---------------------------------------------------------------------------
# Judge execution (real path)
# ---------------------------------------------------------------------------


async def _judge_one_pair(
    judge: LLMJudge,
    criterion: str,
    case_input: str,
    case_output: str,
) -> CriterionResult:
    """Invoke a single `LLMJudge` on (input, output) and unwrap the result."""
    from pydantic_evals.evaluators import EvaluatorContext

    # EvaluatorContext is generic; only `inputs` and `output` are read by
    # LLMJudge.evaluate(). Other context fields default to None / {} which
    # the built-in judge tolerates.
    ctx = EvaluatorContext(
        name=criterion,
        inputs=case_input,
        metadata=None,
        expected_output=None,
        output=case_output,
        duration=0.0,
        _span_tree=None,
        attributes={},
        metrics={},
    )
    raw = await judge.evaluate(ctx)
    return _coerce_judge_result(criterion, raw)


def _coerce_judge_result(criterion: str, raw: Any) -> CriterionResult:
    """Coerce `LLMJudge.evaluate` output into a `CriterionResult`.

    pydantic-evals' built-in `LLMJudge.evaluate` returns a
    `dict[str, EvaluationScalar | EvaluationReason]` keyed by the
    judge's name (with `_score` / `_pass` suffixes when both are
    requested). Each value is either a scalar (bool/int/float/string)
    or an `EvaluationReason(value=..., reason=...)`. We collect any
    numeric/bool entries, prefer numeric scores over booleans, clamp
    into [0, 1], and pull the first available `reason`.

    Also accepts the older `EvaluationReason` direct return shape and
    a `(value, reason)` tuple defensively, so the wrapper survives
    minor API shifts in pydantic-evals.
    """
    items: list[tuple[Any, str | None]] = []
    if isinstance(raw, dict):
        for v in raw.values():
            value = getattr(v, "value", v)
            reason = getattr(v, "reason", None)
            items.append((value, reason))
    elif isinstance(raw, tuple) and len(raw) == 2:
        items.append((raw[0], str(raw[1]) if raw[1] is not None else None))
    else:
        value = getattr(raw, "value", raw)
        reason = getattr(raw, "reason", None)
        items.append((value, reason))

    # Prefer numeric (non-bool) scores; fall back to bool; else 0.
    numeric_scores: list[float] = []
    bool_scores: list[float] = []
    reason_text: str | None = None
    for value, reason in items:
        if reason and reason_text is None:
            reason_text = str(reason)
        if isinstance(value, bool):
            bool_scores.append(1.0 if value else 0.0)
        elif isinstance(value, (int, float)):
            numeric_scores.append(float(value))
        else:
            try:
                numeric_scores.append(float(value))
            except (TypeError, ValueError):
                continue

    if numeric_scores:
        score = sum(numeric_scores) / len(numeric_scores)
    elif bool_scores:
        score = sum(bool_scores) / len(bool_scores)
    else:
        score = 0.0
    score = max(0.0, min(1.0, score))
    return CriterionResult(criterion=criterion, score=score, reason=reason_text)


async def _judge_all_cases(
    judges: dict[str, LLMJudge],
    case_io_pairs: list[tuple[CaseSpec, str]],
) -> list[list[CriterionResult]]:
    """Fan out (case × selected criterion) judging via `asyncio.gather`.

    Returns one list[CriterionResult] per case in input order. Single
    `asyncio.run` entry point from the sync flow body.
    """
    per_case_tasks: list[asyncio.Task[list[CriterionResult]]] = []
    for case_spec, output in case_io_pairs:
        selected = _selected_criteria(case_spec, judges)
        coros = [
            _judge_one_pair(judges[name], name, case_spec.prompt, output)
            for name in selected
        ]
        per_case_tasks.append(asyncio.create_task(_gather_list(coros)))
    return await asyncio.gather(*per_case_tasks)


async def _gather_list(coros: list[Any]) -> list[CriterionResult]:
    if not coros:
        return []
    return list(await asyncio.gather(*coros))


def _selected_criteria(case: CaseSpec, judges: dict[str, LLMJudge]) -> list[str]:
    if case.evaluators:
        # Preserve case-specified order; ignore unknowns rather than 500ing.
        return [name for name in case.evaluators if name in judges]
    return list(judges.keys())


# ---------------------------------------------------------------------------
# Claude Code judge (default; OAuth via Claude.ai subscription)
# ---------------------------------------------------------------------------
#
# Pattern adopted from rocks_project/data-agent's `ClaudeCodeJudge`
# (polymer-dev/polymer/core-services/data-agent/evals/evaluators.py:621).
# Subscription-based, no API keys; the SDK shells the local Claude CLI
# (~/.claude/.credentials.json). One judge call per (case × criterion).


# Default judge turn budget. The rocks_project ClaudeCodeJudge used
# max_turns=12; we go higher because skill rubrics often involve checking
# whether CLI commands / file paths / API surfaces actually exist, and
# a generous budget lets the judge actually investigate. Per-call latency
# is bounded mostly by tool-call I/O, not by the cap. Override with the
# `judge_max_turns` kwarg when you need a different ceiling.
_DEFAULT_JUDGE_MAX_TURNS = 50

_AGENTS_DIR = Path(__file__).parent / "agents"
_JUDGE_ROLE = "skill-evals-judge"


async def _claude_judge_one_pair(
    criterion: str,
    rubric_text: str,
    case_prompt: str,
    response: str,
    *,
    max_turns: int = _DEFAULT_JUDGE_MAX_TURNS,
) -> CriterionResult:
    """One Claude Code judge call for one (criterion, case) pair.

    Uses the Claude Agent SDK over OAuth — no `ANTHROPIC_API_KEY` is
    required (and is unset to force OAuth even if it leaks into env).
    The judge has full Claude Code tool access (Read, Bash, Grep,
    WebSearch, …) and a generous turn budget so it can actually verify
    load-bearing claims (e.g., that a CLI command exists) rather than
    just pattern-matching the text.
    """
    os.environ.pop("ANTHROPIC_API_KEY", None)

    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
    except ImportError as e:
        raise RuntimeError(
            "install `claude-agent-sdk` to run skill-evals with the "
            "claude-code judge backend (default), or pass "
            "`judge_backend=\"pydantic-evals\"` to use the API-key path."
        ) from e

    from prefect_orchestration.templates import render_template

    judge_prompt = render_template(
        _AGENTS_DIR,
        _JUDGE_ROLE,
        case_prompt=case_prompt,
        response=response,
        criterion_name=criterion,
        rubric_text=rubric_text,
    )

    options = ClaudeAgentOptions(max_turns=max_turns)

    result_text = ""
    last_assistant_text = ""
    try:
        async for message in query(prompt=judge_prompt, options=options):
            if hasattr(message, "result") and message.result:
                result_text = (
                    message.result if isinstance(message.result, str)
                    else str(message.result)
                )
            content = getattr(message, "content", None)
            if content and isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if text:
                        last_assistant_text = text
    except Exception as e:
        return CriterionResult(
            criterion=criterion,
            score=0.5,
            reason=f"claude-code judge call failed: {type(e).__name__}: {e}",
        )

    text = result_text or last_assistant_text
    if not text:
        return CriterionResult(
            criterion=criterion, score=0.5, reason="claude-code judge: empty result"
        )

    payload = _extract_judge_json(text)
    if payload is None:
        return CriterionResult(
            criterion=criterion,
            score=0.5,
            reason=f"claude-code judge: no JSON in result ({text[:120]!r})",
        )

    try:
        score = float(payload.get("score", 0.5))
    except (TypeError, ValueError):
        score = 0.5
    score = max(0.0, min(1.0, score))
    reason = str(payload.get("reason", "")) or None
    return CriterionResult(criterion=criterion, score=round(score, 4), reason=reason)


def _extract_judge_json(text: str) -> dict[str, Any] | None:
    """Pull the trailing JSON object from a Claude Code judge response.

    Prefers the last line (per our prompt), falls back to a regex scan
    for `{... "score": ... }` anywhere in the text.
    """
    for candidate in reversed(text.strip().splitlines()):
        s = candidate.strip().strip("`")
        if s.startswith("{") and s.endswith("}"):
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                continue
    m = re.search(r"\{[^{}]*\"score\"\s*:\s*[\d.]+[^{}]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


async def _claude_judge_all_cases(
    rubrics: RubricsFile,
    case_io_pairs: list[tuple[CaseSpec, str]],
    *,
    max_turns: int = _DEFAULT_JUDGE_MAX_TURNS,
) -> list[list[CriterionResult]]:
    """Fan out (case × selected criterion) judge calls via `asyncio.gather`.

    Mirrors `_judge_all_cases` (the pydantic-evals path) — single
    `asyncio.run` entry from the sync flow body; the inner gather
    parallelizes I/O across criteria and across cases.
    """
    rubric_by_name = {c.name: c for c in rubrics.criteria}
    available = list(rubric_by_name.keys())

    per_case_tasks: list[asyncio.Task[list[CriterionResult]]] = []
    for case_spec, output in case_io_pairs:
        selected = case_spec.evaluators or available
        coros = [
            _claude_judge_one_pair(
                name,
                _compose_rubric_text(rubric_by_name[name]),
                case_spec.prompt,
                output,
                max_turns=max_turns,
            )
            for name in selected
            if name in rubric_by_name
        ]
        per_case_tasks.append(asyncio.create_task(_gather_list(coros)))
    return await asyncio.gather(*per_case_tasks)


# ---------------------------------------------------------------------------
# Stub judging (--dry-run) — does NOT import pydantic_evals
# ---------------------------------------------------------------------------


def _stub_judge_all_cases(
    case_io_pairs: list[tuple[CaseSpec, str]],
    rubrics: RubricsFile,
) -> list[list[CriterionResult]]:
    """Deterministic per-criterion scores derived from a hash of (case, criterion).

    Bound to [0.5, 1.0) so by default every stub case passes the 0.75
    rubric threshold. Tests that need a failing case can lower the
    threshold or use a case name that hashes low.
    """
    out: list[list[CriterionResult]] = []
    available = [c.name for c in rubrics.criteria]
    for case_spec, _output in case_io_pairs:
        selected = case_spec.evaluators or available
        per_case: list[CriterionResult] = []
        for crit_name in selected:
            if crit_name not in available:
                continue
            digest = hashlib.sha256(
                f"{case_spec.name}|{crit_name}".encode("utf-8")
            ).digest()
            # Map first two bytes into [0.0, 1.0), then squash into [0.5, 1.0).
            raw = int.from_bytes(digest[:2], "big") / 0x10000
            score = 0.5 + raw / 2.0
            per_case.append(
                CriterionResult(
                    criterion=crit_name,
                    score=round(score, 4),
                    reason="dry-run stub score",
                )
            )
        out.append(per_case)
    return out


# ---------------------------------------------------------------------------
# Agent driver
# ---------------------------------------------------------------------------


def _select_backend(dry_run: bool, *, issue_id: str | None = None) -> Any:
    """Pick a `SessionBackend` factory honoring `--dry-run` + `PO_BACKEND`.

    `TmuxClaudeBackend` requires `issue` + `role` to name its tmux
    session; we pass `issue_id` (or a stable fallback) and the fixed
    `skill-evals` role so concurrent skill-eval runs don't collide.
    """
    if dry_run:
        return StubBackend()
    choice = (os.environ.get("PO_BACKEND") or "").lower()
    if choice == "cli":
        return ClaudeCliBackend()
    if choice == "stub":
        return StubBackend()
    tmux_kwargs = {"issue": issue_id or "skill-evals", "role": "skill-evals"}
    if choice == "tmux":
        if shutil.which("tmux") is None:
            raise RuntimeError("PO_BACKEND=tmux but tmux not on PATH")
        return TmuxClaudeBackend(**tmux_kwargs)
    if shutil.which("tmux"):
        return TmuxClaudeBackend(**tmux_kwargs)
    return ClaudeCliBackend()


def _build_session(
    skill_dir: Path,
    rig_path: Path | None,
    *,
    dry_run: bool,
    issue_id: str | None = None,
) -> AgentSession:
    """Construct one `AgentSession` reused across all cases.

    `repo_path` is the pack's dist root (skill_dir.parent.parent). When
    a real rig is available we prefer it so file ops the agent runs land
    in the rig, not in the pack tree. `skip_mail_inject=True` because
    eval reproducibility must not depend on any agent's inbox state.
    """
    backend = _select_backend(dry_run, issue_id=issue_id)
    repo_path = rig_path if rig_path is not None else skill_dir.parent.parent
    return AgentSession(
        role="skill-evals",
        repo_path=Path(repo_path),
        backend=backend,
        skip_mail_inject=True,
        # Eval runs should not mutate the rig — disable overlay/skills copy.
        overlay=False,
        skills=False,
    )


def _build_skill_prompt(skill_md: Path, case_prompt: str) -> str:
    """Compose the full prompt for one case: SKILL.md preamble + case prompt."""
    try:
        body = skill_md.read_text(encoding="utf-8")
    except FileNotFoundError:
        body = ""
    return f"<skill>\n{body}\n</skill>\n\n{case_prompt}\n"


def drive_skill(
    session: AgentSession,
    skill_md: Path,
    case_prompt: str,
    *,
    fork: bool = True,
) -> str:
    """Run one case through the agent and return its reply text."""
    prompt = _build_skill_prompt(skill_md, case_prompt)
    return session.prompt(prompt, fork=fork)


# ---------------------------------------------------------------------------
# Reports + verdict
# ---------------------------------------------------------------------------


def _render_markdown_report(verdict: SkillEvalsVerdict) -> str:
    lines: list[str] = []
    lines.append(f"# skill-evals: {verdict.pack} / {verdict.skill}")
    lines.append("")
    lines.append(f"- **judge_model**: `{verdict.judge_model or '(library default)'}`")
    lines.append(f"- **tier**: `{verdict.tier or '(all)'}`")
    if verdict.case_filter:
        lines.append(f"- **case filter**: `{verdict.case_filter}`")
    lines.append(f"- **pass threshold**: {verdict.pass_threshold:.2f}")
    lines.append(
        f"- **result**: {verdict.n_passed}/{verdict.n_cases} passed "
        f"({'PASS' if verdict.overall_pass else 'FAIL'})"
    )
    lines.append(f"- **started**: {verdict.started_at}")
    lines.append(f"- **finished**: {verdict.finished_at}")
    lines.append("")
    lines.append("| case | tier | score | pass | criteria |")
    lines.append("|---|---|---|---|---|")
    for c in verdict.cases:
        crits = ", ".join(f"{r.criterion}={r.score:.2f}" for r in c.criteria)
        lines.append(
            f"| {c.case} | {c.tier} | {c.score:.2f} | "
            f"{'PASS' if c.pass_ else 'FAIL'} | {crits} |"
        )
    lines.append("")
    return "\n".join(lines)


def _stamp_skill_marker(skill_md: Path, verdict: SkillEvalsVerdict) -> None:
    """Idempotent single-line marker comment near the bottom of SKILL.md."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    marker = (
        f"<!-- po-skill-evals last-run: {verdict.finished_at} "
        f"n_pass={verdict.n_passed}/{verdict.n_cases} -->"
    )
    new_text = _SKILL_MARKER_RE.sub("", text).rstrip() + "\n\n" + marker + "\n"
    if new_text != text:
        skill_md.write_text(new_text, encoding="utf-8")


def write_reports(skill_dir: Path, verdict: SkillEvalsVerdict) -> tuple[Path, Path]:
    """Write reports/latest.{json,md} next to the skill. Returns (json, md)."""
    reports_dir = skill_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / "latest.json"
    md_path = reports_dir / "latest.md"
    json_path.write_text(
        verdict.model_dump_json(by_alias=True, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(_render_markdown_report(verdict), encoding="utf-8")
    _stamp_skill_marker(skill_dir / "SKILL.md", verdict)
    return json_path, md_path


def _write_run_dir_verdict(
    rig_path: Path, issue_id: str, verdict: SkillEvalsVerdict
) -> Path:
    run_dir = rig_path / ".planning" / "skill-evals" / issue_id
    verdicts_dir = run_dir / "verdicts"
    verdicts_dir.mkdir(parents=True, exist_ok=True)
    path = verdicts_dir / "skill-evals.json"
    path.write_text(
        verdict.model_dump_json(by_alias=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# bd integration (best-effort, only when issue_id + rig_path provided)
# ---------------------------------------------------------------------------


def _bd_set_metadata(rig_path: Path, issue_id: str, **kv: str) -> None:
    if shutil.which("bd") is None or not (rig_path / ".beads").exists():
        return
    args = ["bd", "update", issue_id]
    for k, v in kv.items():
        args.extend(["--set-metadata", f"{k}={v}"])
    subprocess.run(args, cwd=rig_path, capture_output=True, check=False)


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _truncate(text: str, *, max_bytes: int = _OUTPUT_TRUNCATE_BYTES) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore") + "\n…(truncated)"


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


@flow(name="skill_evals")
def skill_evals(
    pack: str,
    skill: str,
    tier: str | None = None,
    case: str | None = None,
    judge_model: str | None = None,
    judge_backend: str = "claude-code",
    judge_max_turns: int = _DEFAULT_JUDGE_MAX_TURNS,
    pass_threshold: float | None = None,
    dry_run: bool = False,
    issue_id: str | None = None,
    rig: str | None = None,  # noqa: ARG001 — accepted for `po run` parity
    rig_path: str | None = None,
) -> dict[str, Any]:
    """Run a skill's evals/ suite and write reports + (optional) verdict.

    `issue_id` / `rig_path` are optional. When both are set, the verdict
    is also dropped under `<rig_path>/.planning/skill-evals/<issue_id>/
    verdicts/skill-evals.json` so other PO tooling (`po artifacts`,
    `po watch`, …) sees it.
    """
    log = get_run_logger()
    started_at = _utcnow_iso()
    started_t = time.time()

    skill_dir = resolve_pack_skill_dir(pack, skill)
    cases_file = load_cases(skill_dir)
    rubrics = load_rubrics(skill_dir)

    selected_cases = filter_cases(cases_file.cases, tier=tier, case=case)
    threshold = pass_threshold if pass_threshold is not None else rubrics.pass_threshold
    effective_judge_model = judge_model or rubrics.judge_model

    rig_path_p = Path(rig_path).expanduser().resolve() if rig_path else None
    if rig_path_p and issue_id:
        run_dir = rig_path_p / ".planning" / "skill-evals" / issue_id
        run_dir.mkdir(parents=True, exist_ok=True)
        _bd_set_metadata(
            rig_path_p,
            issue_id,
            **{"po.rig_path": str(rig_path_p), "po.run_dir": str(run_dir)},
        )

    telemetry = _select_telemetry()
    log.info(
        "skill-evals: pack=%s skill=%s tier=%s n_cases=%d dry_run=%s",
        pack,
        skill,
        tier,
        len(selected_cases),
        dry_run,
    )

    with telemetry.span(
        "skill_evals.run",
        pack=pack,
        skill=skill,
        tier=tier or "",
        case_filter=case or "",
        judge_model=effective_judge_model or "",
        n_cases=len(selected_cases),
        dry_run=dry_run,
    ) as run_span:
        try:
            # Phase 1: drive the agent for each case (one shared session).
            session = _build_session(
                skill_dir, rig_path_p, dry_run=dry_run, issue_id=issue_id
            )
            skill_md = skill_dir / "SKILL.md"
            case_io_pairs: list[tuple[CaseSpec, str]] = []
            elapsed: list[float] = []
            for case_spec in selected_cases:
                t0 = time.time()
                output = drive_skill(session, skill_md, case_spec.prompt, fork=True)
                elapsed.append(time.time() - t0)
                case_io_pairs.append((case_spec, output))

            # Phase 2: judge — single asyncio.run for the whole batch.
            #
            # Backend selection:
            #   dry-run         → deterministic stub scores (no model calls)
            #   claude-code     → Claude Agent SDK over OAuth (default; no API key)
            #   pydantic-evals  → pydantic-evals' built-in LLMJudge (API-key path)
            if dry_run:
                all_criteria = _stub_judge_all_cases(case_io_pairs, rubrics)
            elif judge_backend == "claude-code":
                all_criteria = asyncio.run(
                    _claude_judge_all_cases(
                        rubrics, case_io_pairs, max_turns=judge_max_turns
                    )
                )
            elif judge_backend == "pydantic-evals":
                judges = build_judges(rubrics, default_model=effective_judge_model)
                all_criteria = asyncio.run(_judge_all_cases(judges, case_io_pairs))
            else:
                raise ValueError(
                    f"unknown judge_backend={judge_backend!r}; "
                    "expected 'claude-code', 'pydantic-evals', or use --dry-run"
                )

            # Phase 3: fold per-case results, emit per-case spans.
            case_results: list[CaseResult] = []
            for (case_spec, output), criteria, secs in zip(
                case_io_pairs, all_criteria, elapsed, strict=True
            ):
                with telemetry.span(
                    "skill_evals.case",
                    skill=skill,
                    eval_case=case_spec.name,
                    tier=case_spec.tier,
                ) as case_span:
                    score = _mean([c.score for c in criteria])
                    case_threshold = (
                        case_spec.pass_threshold
                        if case_spec.pass_threshold is not None
                        else threshold
                    )
                    passed = score >= case_threshold
                    case_span.set_attribute("score", score)
                    case_span.set_attribute("pass", passed)
                    case_results.append(
                        CaseResult(
                            case=case_spec.name,
                            tier=case_spec.tier,
                            score=round(score, 4),
                            **{"pass": passed},
                            criteria=criteria,
                            output=_truncate(output),
                            elapsed_seconds=round(secs, 3),
                        )
                    )

            n_passed = sum(1 for c in case_results if c.pass_)
            verdict = SkillEvalsVerdict(
                skill=skill,
                pack=pack,
                judge_model=effective_judge_model,
                tier=tier,
                case_filter=case,
                pass_threshold=threshold,
                n_cases=len(case_results),
                n_passed=n_passed,
                overall_pass=(n_passed == len(case_results) and len(case_results) > 0),
                cases=case_results,
                started_at=started_at,
                finished_at=_utcnow_iso(),
            )

            json_path, md_path = write_reports(skill_dir, verdict)
            run_dir_verdict_path: Path | None = None
            if rig_path_p and issue_id:
                run_dir_verdict_path = _write_run_dir_verdict(
                    rig_path_p, issue_id, verdict
                )

            run_span.set_attribute("n_passed", n_passed)
            run_span.set_attribute("overall_pass", verdict.overall_pass)
            run_span.set_attribute("elapsed_seconds", time.time() - started_t)

            log.info(
                "skill-evals: %d/%d passed; report=%s",
                n_passed,
                len(case_results),
                md_path,
            )
            return {
                "verdict": verdict.model_dump(mode="json", by_alias=True),
                "report_md": str(md_path),
                "report_json": str(json_path),
                "run_dir_verdict": (
                    str(run_dir_verdict_path) if run_dir_verdict_path else None
                ),
            }
        except BaseException as exc:
            run_span.record_exception(exc)
            run_span.set_status("ERROR", str(exc))
            raise
