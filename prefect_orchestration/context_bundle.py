"""Build a single CONTEXT.md bundle per role-step from run-dir artifacts.

Collapses the N `cat` round-trips an agent would otherwise spend reading
plan.md / triage.md / build-iter-*.diff / decision-log.md into one file
read at the start of the turn.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _bd_show(bead_id: str, rig_path: Path) -> str:
    try:
        r = subprocess.run(
            ["bd", "show", bead_id],
            capture_output=True,
            text=True,
            cwd=rig_path,
            timeout=15,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def _read_file(path: Path, max_chars: int = 60_000) -> str:
    if not path.is_file():
        return "(empty)"
    try:
        text = path.read_text()
    except OSError:
        return "(empty)"
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[...truncated at {max_chars} chars]\n"
    return text


def _latest_build_diff(run_dir: Path) -> str:
    diffs = sorted(
        run_dir.glob("build-iter-*.diff"),
        key=lambda p: int(p.stem.split("-")[-1]),
    )
    return _read_file(diffs[-1]) if diffs else "(empty)"


def build_context_md(
    run_dir: Path,
    rig_path: Path,
    issue_id: str,
    role: str,
    iter_n: int | None,
    pack_path: str | None = None,
) -> Path:
    """Write <run_dir>/CONTEXT.md bundling all role-relevant artifacts.

    Idempotent — overwritten on each role-step entry.
    """
    iter_bead_id = f"{issue_id}.{role}.iter{iter_n}" if iter_n is not None else None
    step_text = _bd_show(iter_bead_id, rig_path) if iter_bead_id else "(empty)"

    conventions = "(empty)"
    if pack_path and (Path(pack_path) / "CLAUDE.md").is_file():
        lines = (Path(pack_path) / "CLAUDE.md").read_text().splitlines()[:50]
        conventions = "\n".join(lines)

    sections = [
        f"## Issue\n\n{_bd_show(issue_id, rig_path) or '(empty)'}",
        f"## This role-step\n\n{step_text}",
        f"## Plan\n\n{_read_file(run_dir / 'plan.md')}",
        f"## Triage flags\n\n{_read_file(run_dir / 'triage.md')}",
        f"## Build diff (latest)\n\n{_latest_build_diff(run_dir)}",
        f"## Decision log\n\n{_read_file(run_dir / 'decision-log.md')}",
        f"## Pack-side conventions\n\n{conventions}",
    ]

    out = run_dir / "CONTEXT.md"
    out.write_text("\n\n---\n\n".join(sections) + "\n")
    return out
