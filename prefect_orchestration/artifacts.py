"""Collect + render the forensic trail from any formula's run dir.

Pure helpers used by `po artifacts`. The run dir layout is:

    <rig>/.planning/<formula>/<issue>/
      triage.md
      plan.md
      critique-iter-N.md
      verification-report-iter-N.md
      decision-log.md
      lessons-learned.md
      verdicts/*.json

Iter files are sorted by integer N so `iter-10` comes after `iter-2`.
Missing files render as a header with `(missing)` — never abort.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

ITER_RE = re.compile(r"iter-(\d+)\.md$")

FIXED_SECTIONS: tuple[str, ...] = ("triage.md", "plan.md")
TRAILING_SECTIONS: tuple[str, ...] = ("decision-log.md", "lessons-learned.md")


@dataclass(frozen=True)
class Section:
    """One chunk of the forensic dump — a header and a body."""

    label: str
    path: Path
    body: str


def _iter_n(name: str) -> int | None:
    m = ITER_RE.search(name)
    return int(m.group(1)) if m else None


def _iter_pairs(run_dir: Path) -> list[Path]:
    """critique-iter-N.md + verification-report-iter-N.md sorted by N, critique first."""
    critiques = {
        _iter_n(p.name): p
        for p in run_dir.glob("critique-iter-*.md")
        if _iter_n(p.name) is not None
    }
    verifies = {
        _iter_n(p.name): p
        for p in run_dir.glob("verification-report-iter-*.md")
        if _iter_n(p.name) is not None
    }
    ns = sorted(set(critiques) | set(verifies))
    out: list[Path] = []
    for n in ns:
        if n in critiques:
            out.append(critiques[n])
        if n in verifies:
            out.append(verifies[n])
    return out


def _read_or_missing(path: Path, run_dir: Path) -> Section:
    try:
        rel = path.relative_to(run_dir)
    except ValueError:
        rel = path
    label = str(rel)
    if not path.exists():
        return Section(label=label, path=path, body="(missing)")
    if path.suffix == ".json":
        try:
            data = json.loads(path.read_text())
            body = json.dumps(data, indent=2, sort_keys=True)
        except (OSError, json.JSONDecodeError):
            body = path.read_text(errors="replace")
    else:
        body = path.read_text(errors="replace")
    return Section(label=label, path=path, body=body)


def collect_sections(run_dir: Path, *, verdicts_only: bool = False) -> list[Section]:
    """Ordered list of sections to render.

    verdicts_only=True restricts to `verdicts/*.json` (alphabetical).
    """
    if verdicts_only:
        return _collect_verdicts(run_dir)

    sections: list[Section] = []
    for name in FIXED_SECTIONS:
        sections.append(_read_or_missing(run_dir / name, run_dir))
    for iter_path in _iter_pairs(run_dir):
        sections.append(_read_or_missing(iter_path, run_dir))
    for name in TRAILING_SECTIONS:
        sections.append(_read_or_missing(run_dir / name, run_dir))
    sections.extend(_collect_verdicts(run_dir))
    return sections


def _collect_verdicts(run_dir: Path) -> list[Section]:
    verdicts_dir = run_dir / "verdicts"
    if not verdicts_dir.is_dir():
        return []
    return [
        _read_or_missing(p, run_dir)
        for p in sorted(verdicts_dir.glob("*.json"), key=lambda p: p.name)
    ]


def render(sections: list[Section]) -> str:
    """Plain-ASCII rendering — no ANSI, safe to pipe to `less`."""
    chunks: list[str] = []
    for sec in sections:
        chunks.append(f"===== {sec.label} =====")
        body = sec.body
        chunks.append(body if body.endswith("\n") else body + "\n")
    return "\n".join(chunks)
