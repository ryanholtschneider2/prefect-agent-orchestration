"""Scheduled reflection flow over prior PO and Codex artifacts."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from prefect import flow


KNOWN_ARTIFACTS = ("plan.md", "decision-log.md", "lessons-learned.md")
REPORT_ROOT = Path(".planning") / "update-prompts-from-lessons"
CODEX_EVENT_LOG = Path(".claude") / "logs" / "session-events.jsonl"
CODEX_DIAGNOSTICS_LOG = Path(".claude") / "logs" / "diagnostics.jsonl"
PUSHBACK_MARKERS = (
    "not good enough",
    "too many",
    "should just",
    "why didn't",
    "why did you",
    "instead",
    "actually",
    "no,",
    "ugh",
)


@dataclass(frozen=True)
class ReflectionSignal:
    kind: str
    detail: str
    evidence: str
    explicit: bool = False


@dataclass(frozen=True)
class ImprovementProposal:
    kind: str
    title: str
    summary: str
    evidence: list[str]
    count: int
    explicit: bool
    search_query: str


@dataclass(frozen=True)
class DedupeDecision:
    status: str
    reason: str


@dataclass(frozen=True)
class ReportProposal:
    proposal: ImprovementProposal
    dedupe: DedupeDecision
    bead_id: str | None = None


@dataclass(frozen=True)
class ReflectionEvidence:
    source: str
    signals: list[ReflectionSignal]


def _known_artifact_paths(run_dir: Path) -> list[Path]:
    artifact_paths = [
        run_dir / name for name in KNOWN_ARTIFACTS if (run_dir / name).exists()
    ]
    verdict_dir = run_dir / "verdicts"
    if verdict_dir.exists():
        artifact_paths.extend(sorted(verdict_dir.glob("*.json")))
    return artifact_paths


def _latest_artifact_mtime(run_dir: Path) -> float | None:
    artifact_paths = _known_artifact_paths(run_dir)
    if not artifact_paths:
        return None
    return max(path.stat().st_mtime for path in artifact_paths)


def collect_run_dirs(rig_path: Path, since_days: int) -> list[Path]:
    planning_root = rig_path / ".planning"
    if not planning_root.exists():
        return []
    cutoff = datetime.now(UTC) - timedelta(days=since_days)
    run_dirs: list[Path] = []
    for formula_dir in sorted(planning_root.iterdir()):
        if not formula_dir.is_dir():
            continue
        for run_dir in sorted(formula_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            latest_mtime = _latest_artifact_mtime(run_dir)
            if latest_mtime is None:
                continue
            if datetime.fromtimestamp(latest_mtime, tz=UTC) < cutoff:
                continue
            run_dirs.append(run_dir)
    return run_dirs


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _normalize_line(line: str) -> str:
    return " ".join(line.strip().split())


def _explicit_line(kind: str, line: str) -> ReflectionSignal | None:
    lowered = line.lower()
    if kind not in lowered:
        return None
    if not any(
        word in lowered
        for word in ("need", "missing", "should", "add", "create", "improve")
    ):
        return None
    detail = _normalize_line(line.lstrip("-*0123456789. "))
    return ReflectionSignal(
        kind=kind,
        detail=detail,
        evidence=line,
        explicit=True,
    )


def extract_signals(run_dir: Path) -> list[ReflectionSignal]:
    signals: list[ReflectionSignal] = []

    verdict_dir = run_dir / "verdicts"
    for verdict_path in (
        sorted(verdict_dir.glob("*.json")) if verdict_dir.exists() else []
    ):
        payload = _load_json(verdict_path)
        haystack = json.dumps(payload).lower()
        if any(word in haystack for word in ("reject", "rejected", "needs_revision")):
            signals.append(
                ReflectionSignal(
                    kind="workflow",
                    detail="Repeated critic rejection suggests a reusable workflow guard.",
                    evidence=str(verdict_path.relative_to(run_dir)),
                )
            )
        if "test" in haystack and any(
            word in haystack for word in ("fail", "failed", "error")
        ):
            signals.append(
                ReflectionSignal(
                    kind="hook",
                    detail="Repeated test failures suggest a pre-submit validation hook.",
                    evidence=str(verdict_path.relative_to(run_dir)),
                )
            )
        if "lint" in haystack and any(
            word in haystack for word in ("fail", "failed", "error")
        ):
            signals.append(
                ReflectionSignal(
                    kind="hook",
                    detail="Repeated lint failures suggest a lint guard or helper.",
                    evidence=str(verdict_path.relative_to(run_dir)),
                )
            )

    for artifact_name in ("decision-log.md", "lessons-learned.md"):
        artifact_path = run_dir / artifact_name
        if not artifact_path.exists():
            continue
        for raw_line in artifact_path.read_text().splitlines():
            line = _normalize_line(raw_line)
            if not line:
                continue
            for kind in ("skill", "hook", "workflow", "agent"):
                signal = _explicit_line(kind, line)
                if signal is not None:
                    signals.append(signal)

    return signals


def _parse_jsonl(path: Path) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    if not path.exists():
        return payloads
    for raw_line in path.read_text(errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            payloads.append(parsed)
    return payloads


def _recent_event_payloads(rig_path: Path, since_days: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(UTC) - timedelta(days=since_days)
    payloads: list[dict[str, Any]] = []
    for payload in _parse_jsonl(rig_path / CODEX_EVENT_LOG):
        captured_at = payload.get("captured_at")
        if not isinstance(captured_at, str):
            continue
        try:
            captured_dt = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if captured_dt < cutoff:
            continue
        payloads.append(payload)
    return payloads


def extract_codex_log_signals(
    rig_path: Path, since_days: int
) -> list[ReflectionSignal]:
    signals: list[ReflectionSignal] = []
    event_payloads = _recent_event_payloads(rig_path, since_days)
    if not event_payloads:
        return signals

    by_session: dict[str, list[dict[str, Any]]] = {}
    for payload in event_payloads:
        session_id = str(payload.get("session_id") or "")
        if not session_id:
            continue
        by_session.setdefault(session_id, []).append(payload)

    for session_id, items in by_session.items():
        sorted_items = sorted(items, key=lambda item: str(item.get("captured_at", "")))
        prompt_count = 0
        for payload in sorted_items:
            if payload.get("hook_event_name") != "UserPromptSubmit":
                continue
            prompt_count += 1
            prompt_text = str(payload.get("prompt") or "")
            lowered = prompt_text.lower()

            if any(marker in lowered for marker in PUSHBACK_MARKERS):
                signals.append(
                    ReflectionSignal(
                        kind="agent",
                        detail="User pushback in Codex sessions suggests tightening default agent behavior or references.",
                        evidence=f"codex-log:{session_id}:prompt-{prompt_count}",
                    )
                )
            if "skill" in lowered and any(
                word in lowered for word in ("add", "improve", "make", "expand")
            ):
                signals.append(
                    ReflectionSignal(
                        kind="skill",
                        detail="Repeated requests to add or improve skills suggest a reusable setup skill gap.",
                        evidence=f"codex-log:{session_id}:prompt-{prompt_count}",
                        explicit=True,
                    )
                )
            if any(
                word in lowered
                for word in (
                    "artifact",
                    "diagram",
                    "mermaid",
                    "visual",
                    "screenshot",
                    "demo video",
                )
            ):
                signals.append(
                    ReflectionSignal(
                        kind="workflow",
                        detail="User repeatedly asks for easier-to-grok artifacts, diagrams, or proof outputs in closeout.",
                        evidence=f"codex-log:{session_id}:prompt-{prompt_count}",
                        explicit=True,
                    )
                )
            if "too many" in lowered and "question" in lowered:
                signals.append(
                    ReflectionSignal(
                        kind="agent",
                        detail="Codex asks too many minor decision questions when the next step should be obvious.",
                        evidence=f"codex-log:{session_id}:prompt-{prompt_count}",
                        explicit=True,
                    )
                )

    return signals


def extract_diagnostics_signals(
    rig_path: Path, since_days: int
) -> list[ReflectionSignal]:
    signals: list[ReflectionSignal] = []
    cutoff = datetime.now(UTC) - timedelta(days=since_days)
    for payload in _parse_jsonl(rig_path / CODEX_DIAGNOSTICS_LOG):
        ts = payload.get("timestamp")
        if not isinstance(ts, str):
            continue
        try:
            captured_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if captured_dt < cutoff:
            continue

        message = str(payload.get("message") or "")
        detail = str(payload.get("detail") or "")
        if "OTLP export failed" in message or "curl failed" in message:
            signals.append(
                ReflectionSignal(
                    kind="hook",
                    detail="Logfire export failures suggest the Codex hook/tracing setup needs hardening.",
                    evidence=f"codex-diagnostics:{message}:{detail}",
                )
            )
        if "Could not acquire session lock" in message:
            signals.append(
                ReflectionSignal(
                    kind="hook",
                    detail="Logfire session-lock contention suggests the Codex tracing hook needs concurrency hardening.",
                    evidence=f"codex-diagnostics:{message}",
                )
            )

    return signals


def gather_reflection_evidence(
    rig_path: Path, since_days: int
) -> list[ReflectionEvidence]:
    run_dirs = collect_run_dirs(rig_path, since_days=since_days)
    evidence: list[ReflectionEvidence] = []
    for run_dir in run_dirs:
        evidence.append(
            ReflectionEvidence(
                source=str(run_dir.relative_to(rig_path)),
                signals=extract_signals(run_dir),
            )
        )
    codex_signals = extract_codex_log_signals(rig_path, since_days)
    if codex_signals:
        evidence.append(
            ReflectionEvidence(source=str(CODEX_EVENT_LOG), signals=codex_signals)
        )
    diag_signals = extract_diagnostics_signals(rig_path, since_days)
    if diag_signals:
        evidence.append(
            ReflectionEvidence(source=str(CODEX_DIAGNOSTICS_LOG), signals=diag_signals)
        )
    return evidence


def build_proposals(
    evidence_items: list[ReflectionEvidence],
) -> list[ImprovementProposal]:
    grouped: dict[tuple[str, str], list[ReflectionSignal]] = {}
    for evidence in evidence_items:
        for signal in evidence.signals:
            key = (signal.kind, signal.detail)
            grouped.setdefault(key, []).append(
                ReflectionSignal(
                    kind=signal.kind,
                    detail=signal.detail,
                    evidence=f"{evidence.source}:{signal.evidence}",
                    explicit=signal.explicit,
                )
            )

    proposals: list[ImprovementProposal] = []
    for (kind, detail), signals in sorted(grouped.items()):
        explicit = any(signal.explicit for signal in signals)
        if len(signals) < 2 and not explicit:
            continue
        noun = {
            "skill": "skill",
            "hook": "hook",
            "workflow": "workflow",
            "agent": "agent behavior",
        }[kind]
        proposals.append(
            ImprovementProposal(
                kind=kind,
                title=f"Improve {noun}: {detail[:80]}",
                summary=detail,
                evidence=[signal.evidence for signal in signals],
                count=len(signals),
                explicit=explicit,
                search_query=detail[:60],
            )
        )

    return proposals


def _repo_capability_names(rig_path: Path) -> set[str]:
    names: set[str] = set()
    candidate_roots = [
        rig_path / "skills",
        rig_path / ".beads" / "hooks",
        rig_path / "engdocs",
        rig_path / "packs",
    ]
    for root in candidate_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            names.add(path.name.lower())
    return names


def _query_existing_beads(rig_path: Path, query: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["bd", "search", query, "--status", "all", "--json"],
        cwd=rig_path,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("issues", "results", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def dedupe_proposal(rig_path: Path, proposal: ImprovementProposal) -> DedupeDecision:
    capability_names = _repo_capability_names(rig_path)
    lowered_query = proposal.search_query.lower()
    if any(
        token in name
        for token in lowered_query.split()
        for name in capability_names
        if len(token) > 3
    ):
        return DedupeDecision(
            status="covered",
            reason="A similarly named skill, hook, pack, or doc already exists in the repo.",
        )

    if _query_existing_beads(rig_path, proposal.search_query):
        return DedupeDecision(
            status="existing_bead",
            reason="A similar beads issue already exists.",
        )

    return DedupeDecision(
        status="new", reason="No similar local capability or bead found."
    )


def file_follow_up_bead(rig_path: Path, proposal: ImprovementProposal) -> str | None:
    result = subprocess.run(
        [
            "bd",
            "create",
            "--title",
            proposal.title,
            "--description",
            proposal.summary,
            "--type",
            "task",
            "--priority",
            "2",
            "--silent",
        ],
        cwd=rig_path,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    bead_id = result.stdout.strip()
    return bead_id or None


def write_report(report_dir: Path, report: dict[str, Any]) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    markdown_lines = [
        "# Reflection report",
        "",
        f"- Generated at: {report['generated_at']}",
        f"- Analyzed runs: {len(report['analyzed_runs'])}",
        f"- Analyzed sources: {len(report.get('analyzed_sources', []))}",
        f"- Candidate proposals: {len(report['proposals'])}",
        "",
    ]
    if report.get("analyzed_sources"):
        markdown_lines.append("## Sources")
        markdown_lines.append("")
        for source in report["analyzed_sources"]:
            markdown_lines.append(f"- `{source}`")
        markdown_lines.append("")
    if report["proposals"]:
        markdown_lines.append("## Proposals")
        markdown_lines.append("")
        for item in report["proposals"]:
            markdown_lines.append(f"### {item['title']}")
            markdown_lines.append(f"- Kind: {item['kind']}")
            markdown_lines.append(f"- Count: {item['count']}")
            markdown_lines.append(
                f"- Dedupe: {item['dedupe_status']} ({item['dedupe_reason']})"
            )
            if item["bead_id"]:
                markdown_lines.append(f"- Bead: {item['bead_id']}")
            markdown_lines.append("- Evidence:")
            for evidence in item["evidence"]:
                markdown_lines.append(f"  - {evidence}")
            markdown_lines.append("")
    else:
        markdown_lines.extend(["No repeated signals crossed the filing threshold.", ""])

    (report_dir / "report.md").write_text("\n".join(markdown_lines))
    (report_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True)
    )


def _serialize_report_proposal(item: ReportProposal) -> dict[str, Any]:
    payload = asdict(item.proposal)
    payload["dedupe_status"] = item.dedupe.status
    payload["dedupe_reason"] = item.dedupe.reason
    payload["bead_id"] = item.bead_id
    return payload


@flow(
    name="update_prompts_from_lessons",
    flow_run_name="{report_slug}",
    log_prints=True,
)
def update_prompts_from_lessons(
    rig_path: str,
    lookback_days: int = 7,
    auto_file_beads: bool = False,
    max_proposals: int = 3,
    report_slug: str | None = None,
) -> dict[str, Any]:
    rig = Path(rig_path).expanduser().resolve()
    evidence_items = gather_reflection_evidence(rig, since_days=lookback_days)
    run_sources = [
        item.source for item in evidence_items if item.source.startswith(".planning/")
    ]
    proposals = build_proposals(evidence_items)

    reviewed: list[ReportProposal] = []
    for proposal in proposals[:max_proposals]:
        decision = dedupe_proposal(rig, proposal)
        bead_id = None
        if auto_file_beads and decision.status == "new":
            bead_id = file_follow_up_bead(rig, proposal)
        reviewed.append(
            ReportProposal(proposal=proposal, dedupe=decision, bead_id=bead_id)
        )

    slug = report_slug or datetime.now(UTC).strftime("%Y-%m-%d")
    report_dir = rig / REPORT_ROOT / slug
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "analyzed_runs": run_sources,
        "analyzed_sources": [item.source for item in evidence_items],
        "proposals": [_serialize_report_proposal(item) for item in reviewed],
    }
    write_report(report_dir, report)
    filed_beads = [item.bead_id for item in reviewed if item.bead_id]
    return {
        "status": "ok",
        "report_dir": str(report_dir),
        "proposal_count": len(reviewed),
        "filed_beads": filed_beads,
    }
