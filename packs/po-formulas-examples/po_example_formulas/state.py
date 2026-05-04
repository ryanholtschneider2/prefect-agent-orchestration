"""Rig-local state helpers for the example formula pack.

The pack deliberately uses a filesystem-backed `.po-example/` tree so
the flows can run in a throwaway git repo with no external services.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExampleState:
    rig_path: Path

    @property
    def root(self) -> Path:
        return self.rig_path / ".po-example"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def mail_path(self, role: str) -> Path:
        return self.root / "mail" / f"{role}.json"

    def ready_path(self, role: str) -> Path:
        return self.root / "ready" / f"{role}.json"

    def inbox_untriaged_dir(self, account: str) -> Path:
        return self.root / "inbox" / account / "untriaged"

    def inbox_triaged_dir(self, account: str) -> Path:
        return self.root / "inbox" / account / "triaged"

    def beads_dir(self) -> Path:
        return self.root / "beads"

    def drafts_dir(self) -> Path:
        return self.root / "drafts"

    def run_log_path(self, name: str) -> Path:
        return self.root / f"{name}.jsonl"

    def last_prompt_path(self, role: str) -> Path:
        return self.root / "prompts" / f"{role}.txt"

    def read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        return json.loads(path.read_text())

    def write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True))

    def append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")

    def load_role_mail(self, role: str) -> list[dict[str, Any]]:
        return list(self.read_json(self.mail_path(role), default=[]))

    def save_role_mail(self, role: str, rows: list[dict[str, Any]]) -> None:
        self.write_json(self.mail_path(role), rows)

    def load_ready(self, role: str) -> list[dict[str, Any]]:
        return list(self.read_json(self.ready_path(role), default=[]))

    def save_ready(self, role: str, rows: list[dict[str, Any]]) -> None:
        self.write_json(self.ready_path(role), rows)

    def list_untriaged(self, account: str, limit: int) -> list[tuple[Path, dict[str, Any]]]:
        inbox_dir = self.inbox_untriaged_dir(account)
        if not inbox_dir.exists():
            return []
        out: list[tuple[Path, dict[str, Any]]] = []
        for path in sorted(inbox_dir.glob("*.json"))[:limit]:
            out.append((path, json.loads(path.read_text())))
        return out

    def archive_triaged(
        self, account: str, src_path: Path, payload: dict[str, Any]
    ) -> Path:
        dst = self.inbox_triaged_dir(account) / src_path.name
        self.write_json(dst, payload)
        src_path.unlink()
        return dst

    def write_bead(self, bead_id: str, payload: dict[str, Any]) -> Path:
        path = self.beads_dir() / f"{bead_id}.json"
        self.write_json(path, payload)
        return path

    def read_bead(self, bead_id: str) -> dict[str, Any] | None:
        path = self.beads_dir() / f"{bead_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())
