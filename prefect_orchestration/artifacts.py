"""Collect + render the forensic trail from any formula's run dir.

Pure helpers used by `po artifacts`. The run dir layout is:

    <rig>/.planning/<formula>/<issue>/
      triage.md
      plan.md
      critique-iter-N.md
      verification-report-iter-N.md
      decision-log.md
      lessons-learned.md

Verdicts live on bd-metadata of the iter beads
(`<issue>-<step>-iter<N>`, metadata key `po.<step>`); this module reads
them via `bd list --parent <issue>` and renders them as sections.
Pre-migration run dirs with `verdicts/*.json` files are still rendered
via a legacy fallback in `_collect_verdicts`.

Iter files are sorted by integer N so `iter-10` comes after `iter-2`.
Missing files render as a header with `(missing)` — never abort.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from prefect_orchestration.artifact_contract import ARTIFACT_MANIFEST, REVIEW_SUMMARY

ITER_RE = re.compile(r"iter-(\d+)\.md$")

FIXED_SECTIONS: tuple[str, ...] = ("triage.md", "plan.md")
PROOF_SECTIONS: tuple[str, ...] = (REVIEW_SUMMARY, ARTIFACT_MANIFEST)
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
    for name in PROOF_SECTIONS:
        sections.append(_read_or_missing(run_dir / name, run_dir))
    for name in TRAILING_SECTIONS:
        sections.append(_read_or_missing(run_dir / name, run_dir))
    sections.extend(_collect_verdicts(run_dir))
    return sections


def _collect_verdicts(run_dir: Path) -> list[Section]:
    """Render per-step verdicts as Section objects.

    Source of truth: bd-metadata on iter beads (`<seed>-<step>-iter<N>`).
    By convention, `run_dir.name` is the seed_id. Falls back to the
    legacy `<run_dir>/verdicts/*.json` scan for pre-migration data.
    """
    sections: list[Section] = []
    seed_id = run_dir.name
    if seed_id:
        import subprocess as _sp

        from prefect_orchestration.beads_meta import iter_bead_re

        # Find the rig root by walking up: <rig>/.planning/<formula>/<seed>/
        rig_root: Path | None = None
        candidate = run_dir
        for _ in range(5):
            candidate = candidate.parent
            if (candidate / ".beads").is_dir():
                rig_root = candidate
                break

        proc = _sp.run(
            ["bd", "list", "--parent", seed_id, "--all", "--json"],
            cwd=str(rig_root) if rig_root else None,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            try:
                rows = json.loads(proc.stdout)
            except json.JSONDecodeError:
                rows = []
            iter_pat = iter_bead_re(seed_id)
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                m = iter_pat.match(str(row.get("id", "")))
                if not m:
                    continue
                metadata = row.get("metadata") or {}
                for key, value in sorted(metadata.items()):
                    if not str(key).startswith("po."):
                        continue
                    if key in {"po.run_dir", "po.rig_path"}:
                        continue
                    label = f"{m.group(1)}-iter-{m.group(2)} {key}"
                    body = (
                        json.dumps(value, indent=2)
                        if isinstance(value, (dict, list))
                        else str(value)
                    )
                    sections.append(Section(label=label, path=run_dir, body=body))

    # Legacy fallback for pre-migration run dirs.
    legacy_dir = run_dir / "verdicts"
    if legacy_dir.is_dir():
        sections.extend(
            _read_or_missing(p, run_dir)
            for p in sorted(legacy_dir.glob("*.json"), key=lambda p: p.name)
        )
    return sections


def render(sections: list[Section]) -> str:
    """Plain-ASCII rendering — no ANSI, safe to pipe to `less`."""
    chunks: list[str] = []
    for sec in sections:
        chunks.append(f"===== {sec.label} =====")
        body = sec.body
        chunks.append(body if body.endswith("\n") else body + "\n")
    return "\n".join(chunks)
