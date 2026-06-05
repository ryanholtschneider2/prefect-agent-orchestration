"""Build a single CONTEXT.md bundle per role-step from run-dir artifacts.

Collapses the N `cat` round-trips an agent would otherwise spend reading
plan.md / triage.md / build-iter-*.diff / decision-log.md into one file
read at the start of the turn.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from prefect_orchestration import iter_bead_ids
from prefect_orchestration.beads_meta import _resolve_binary


def _bd_show(bead_id: str, rig_path: Path) -> str:
    # Resolve the right beads binary for this rig (`bd` on dolt, `br` on
    # beads_rust). Hardcoding `bd` returns nothing on a br-only rig.
    binary = _resolve_binary(rig_path) or "bd"
    try:
        r = subprocess.run(
            [binary, "show", bead_id],
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
    iter_bead_id: str | None = None,
) -> Path:
    """Write <run_dir>/CONTEXT.md bundling all role-relevant artifacts.

    Idempotent — overwritten on each role-step entry.

    ``iter_bead_id`` — the backend-assigned id of this role-step's iter bead.
    When omitted, it is resolved from the run-dir iter-bead-id map (recorded
    by ``agent_step`` after ``create_child_bead``), falling back to the
    ``<issue>.<role>.iterN`` convention id. On br rigs the convention id is a
    phantom that ``br show`` can't resolve, so without the map the
    "This role-step" section would be empty and send the agent hunting for a
    non-existent bead; on dolt the convention id is the real id, so the
    fallback is correct there.
    """
    if iter_bead_id is None and iter_n is not None:
        convention_key = iter_bead_ids.convention_id(issue_id, role, iter_n)
        iter_bead_id = iter_bead_ids.lookup(run_dir, convention_key) or convention_key
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
