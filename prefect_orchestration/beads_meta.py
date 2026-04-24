"""Minimal `bd` CLI wrapper for parent-molecule metadata.

The software-dev-full formula uses beads metadata as the shared state
bus between steps (iter counters, verdicts, run_dir, feature flags).
We mirror that here so role prompts that read `bd show <parent>` work
unchanged.

For prototype/local runs without beads installed, `FileStore` falls
back to a JSON file under `$RUN_DIR/metadata.json`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class MetadataStore(Protocol):
    def get(self, key: str, default: str | None = None) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def all(self) -> dict[str, str]: ...


@dataclass
class BeadsStore:
    """Reads/writes metadata on a beads parent molecule."""

    parent_id: str

    def get(self, key: str, default: str | None = None) -> str | None:
        out = subprocess.run(
            ["bd", "show", self.parent_id, "--json"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        meta = json.loads(out).get("metadata") or {}
        return meta.get(key, default)

    def set(self, key: str, value: str) -> None:
        subprocess.run(
            ["bd", "update", self.parent_id, "--metadata", f"{key}={value}"],
            check=True,
        )

    def all(self) -> dict[str, str]:
        out = subprocess.run(
            ["bd", "show", self.parent_id, "--json"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return json.loads(out).get("metadata") or {}


@dataclass
class FileStore:
    """Local-file fallback: `$RUN_DIR/metadata.json`."""

    path: Path

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def _dump(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2))

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._load().get(key, default)

    def set(self, key: str, value: str) -> None:
        data = self._load()
        data[key] = value
        self._dump(data)

    def all(self) -> dict[str, str]:
        return self._load()


def auto_store(parent_id: str | None, run_dir: Path) -> MetadataStore:
    """Use beads if available and parent_id given; else file store."""
    if parent_id and shutil.which("bd"):
        return BeadsStore(parent_id=parent_id)
    return FileStore(path=run_dir / "metadata.json")


def _bd_available() -> bool:
    return shutil.which("bd") is not None


def claim_issue(issue_id: str, assignee: str) -> None:
    """Mark a beads issue in_progress + claim it. No-op if bd missing."""
    if not _bd_available():
        return
    subprocess.run(
        ["bd", "update", issue_id, "--status", "in_progress", "--assignee", assignee],
        check=False,
    )


def close_issue(issue_id: str, notes: str | None = None) -> None:
    """Close a beads issue. No-op if bd missing."""
    if not _bd_available():
        return
    cmd = ["bd", "close", issue_id]
    if notes:
        cmd += ["--reason", notes]
    subprocess.run(cmd, check=False)


def list_epic_children(epic_id: str) -> list[dict]:
    """Return [{'id', 'status', 'dependencies': [id,...]}, ...] for an epic's children.

    Beads epic→child link is by **ID prefix convention** (`<epic>.<N>`), not
    a `parent_id` field — `bd list --parent` returns empty for most epics.
    We probe `<epic>.1`, `<epic>.2`, ... sequentially until we hit a gap.

    Only returns open/in_progress children; closed ones are already done.
    """
    if not _bd_available():
        return []
    children = []
    consecutive_missing = 0
    n = 0
    while consecutive_missing < 3:
        n += 1
        candidate = f"{epic_id}.{n}"
        proc = subprocess.run(
            ["bd", "show", candidate, "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            consecutive_missing += 1
            continue
        consecutive_missing = 0
        try:
            rows = json.loads(proc.stdout)
            row = rows[0] if isinstance(rows, list) else rows
        except (json.JSONDecodeError, IndexError):
            continue
        if row.get("status") in ("open", "in_progress"):
            deps = [
                d["id"] if isinstance(d, dict) else d
                for d in row.get("dependencies") or []
            ]
            children.append(
                {
                    "id": row["id"],
                    "status": row["status"],
                    "dependencies": deps,
                    "title": row.get("title", ""),
                }
            )
    return children
