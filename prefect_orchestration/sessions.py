"""Helpers for `po sessions <issue-id>` — per-role Claude session UUIDs.

The `software-dev-full` pack writes `metadata.json` at the run_dir root with
flat string keys, including `session_<role> = <uuid>`. There's no stored
per-role iter / last-updated, so we derive them by inspecting role-specific
artifact files in the run_dir.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

METADATA_FILENAME = "metadata.json"
SESSION_PREFIX = "session_"

# Role → ordered list of artifact globs. The first glob is preferred; we fall
# back to later ones. Iteration number (when present) is parsed from
# `...-iter-N...` in the filename. Kept module-level so it's easy to extend.
ROLE_ARTIFACT_GLOBS: dict[str, tuple[str, ...]] = {
    "triager": ("triage.md",),
    "planner": ("plan-critique-iter-*.md", "plan.md"),
    "builder": ("build-iter-*.diff",),
    "critic": ("critique-iter-*.md",),
    "verifier": ("verification-report-iter-*.md",),
    "linter": ("lint-iter-*.log",),
    "tester": ("unit-iter-*.log", "e2e-iter-*.log"),
    "releaser": ("decision-log.md",),
    "cleaner": ("lessons-learned.md",),
    "documenter": ("final-tests.txt",),
}

_ITER_RE = re.compile(r"-iter-(\d+)")


class MetadataNotFound(RuntimeError):
    """`metadata.json` is missing from the run_dir."""


@dataclass(frozen=True)
class SessionRow:
    role: str
    uuid: str
    last_iter: str  # string so "-" fits alongside numbers
    last_updated: str  # ISO-8601 local seconds, or "-"


def load_metadata(run_dir: Path) -> dict[str, str]:
    """Read metadata.json from a run_dir. Raises MetadataNotFound if absent."""
    path = run_dir / METADATA_FILENAME
    if not path.exists():
        raise MetadataNotFound(
            f"no {METADATA_FILENAME} in {run_dir}. "
            "The flow may not have completed the session-stamping step yet."
        )
    return json.loads(path.read_text())


def _role_from_key(key: str) -> str | None:
    if not key.startswith(SESSION_PREFIX):
        return None
    return key[len(SESSION_PREFIX) :] or None


def _fmt_mtime(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _artifact_info(run_dir: Path, role: str, meta_mtime: float) -> tuple[str, str]:
    """Return (last_iter, last_updated) for a role by scanning artifacts.

    Falls back to ("-", formatted meta_mtime) when no matching artifact file
    exists (covers known roles with artifacts not yet written, and unknown
    roles not in the mapping).
    """
    globs = ROLE_ARTIFACT_GLOBS.get(role, ())
    matches: list[Path] = []
    for pattern in globs:
        matches.extend(run_dir.glob(pattern))
    if not matches:
        return "-", _fmt_mtime(meta_mtime)
    freshest = max(matches, key=lambda p: p.stat().st_mtime)
    iter_match = _ITER_RE.search(freshest.name)
    last_iter = iter_match.group(1) if iter_match else "-"
    # Max iter across all matches (filename-based) when multiple iters exist
    iter_nums = [
        int(m.group(1))
        for m in (_ITER_RE.search(p.name) for p in matches)
        if m is not None
    ]
    if iter_nums:
        last_iter = str(max(iter_nums))
    return last_iter, _fmt_mtime(freshest.stat().st_mtime)


def build_rows(run_dir: Path, metadata: dict[str, str]) -> list[SessionRow]:
    """Collect one SessionRow per `session_<role>` key in metadata, sorted by role."""
    meta_path = run_dir / METADATA_FILENAME
    meta_mtime = meta_path.stat().st_mtime if meta_path.exists() else 0.0
    rows: list[SessionRow] = []
    for key, value in metadata.items():
        role = _role_from_key(key)
        if role is None:
            continue
        last_iter, last_updated = _artifact_info(run_dir, role, meta_mtime)
        rows.append(
            SessionRow(
                role=role,
                uuid=str(value),
                last_iter=last_iter,
                last_updated=last_updated,
            )
        )
    rows.sort(key=lambda r: r.role)
    return rows


def render_table(rows: list[SessionRow]) -> str:
    """Width-aligned text table. Empty rows list still renders the header."""
    headers = ("ROLE", "UUID", "LAST-ITER", "LAST-UPDATED")
    data = [(r.role, r.uuid, r.last_iter, r.last_updated) for r in rows]
    widths = [
        max(len(h), *(len(row[i]) for row in data)) if data else len(h)
        for i, h in enumerate(headers)
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
    lines.extend(fmt.format(*row) for row in data)
    return "\n".join(lines)


def resume_command(uuid: str) -> str:
    """The copy-paste one-liner for `claude --resume <uuid>`."""
    return f"claude --print --resume {uuid} --fork-session"


def lookup_session(metadata: dict[str, str], role: str) -> str | None:
    """Return the session uuid for a role, or None if not recorded."""
    value = metadata.get(f"{SESSION_PREFIX}{role}")
    return str(value) if value else None
