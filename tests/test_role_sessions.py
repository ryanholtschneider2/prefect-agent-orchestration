"""Unit tests for `role_sessions.RoleSessionStore`.

Verifies the three-tier read order (BeadsStore → role-sessions.json →
legacy metadata.json), the "set never mutates legacy" property, and
the atomic JSON write.

Backs prefect-orchestration-7vs.2 AC (c) — migration shim — plus the
seed-keyed write semantics that AC (a) depends on.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from prefect_orchestration import beads_meta, role_sessions
from prefect_orchestration.role_sessions import (
    ROLE_SESSIONS_FILENAME,
    RoleSessionStore,
)


# ─────────────────────── shared fakes ────────────────────────────────


class _FakeBd:
    """Minimal bd shim: shows + update recorder."""

    def __init__(self, shows: dict[str, Any] | None = None) -> None:
        self.shows: dict[str, Any] = shows or {}
        self.updates: list[list[str]] = []

    def __call__(self, cmd: list[str], **kw: Any) -> subprocess.CompletedProcess:
        if cmd[:2] == ["bd", "show"]:
            issue = cmd[2]
            payload = self.shows.get(issue)
            if payload is None:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(payload), stderr=""
            )
        if cmd[:2] == ["bd", "update"]:
            self.updates.append(list(cmd))
            # Echo set-metadata back into shows so subsequent get sees it.
            issue = cmd[2]
            for i, tok in enumerate(cmd):
                if tok == "--set-metadata" and i + 1 < len(cmd):
                    k, _, v = cmd[i + 1].partition("=")
                    payload = self.shows.setdefault(
                        issue, {"id": issue, "metadata": {}}
                    )
                    if isinstance(payload, list):
                        # bd-show list shape; not used by tests here.
                        payload = payload[0] if payload else {"id": issue}
                        self.shows[issue] = payload
                    payload.setdefault("metadata", {})[k] = v
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


@pytest.fixture
def fake_bd(monkeypatch: pytest.MonkeyPatch) -> _FakeBd:
    fake = _FakeBd()
    monkeypatch.setattr(beads_meta.subprocess, "run", fake)
    monkeypatch.setattr(beads_meta.shutil, "which", lambda _name: "/usr/bin/bd")
    return fake


@pytest.fixture
def no_bd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(beads_meta.shutil, "which", lambda _name: None)


# ─────────────────────── tier ordering ───────────────────────────────


def test_get_returns_none_when_all_tiers_empty(
    fake_bd: _FakeBd, tmp_path: Path
) -> None:
    fake_bd.shows = {"S": {"id": "S", "metadata": {}}}
    store = RoleSessionStore(seed_id="S", seed_run_dir=tmp_path / "S")
    assert store.get("builder") is None


def test_get_reads_legacy_when_only_legacy_present(no_bd: None, tmp_path: Path) -> None:
    """Migration shim hit: legacy metadata.json read when no other tier has the key."""
    legacy_run = tmp_path / "issue1"
    legacy_run.mkdir()
    (legacy_run / "metadata.json").write_text(
        json.dumps({"session_builder": "legacy-uuid", "other": "ignored"})
    )
    store = RoleSessionStore(
        seed_id="seed",
        seed_run_dir=tmp_path / "seed",
        legacy_self_run_dir=legacy_run,
    )
    assert store.get("builder") == "legacy-uuid"
    # Non-session keys are not exposed.
    assert store.get("other") is None


def test_json_overrides_legacy(no_bd: None, tmp_path: Path) -> None:
    """role-sessions.json wins over legacy metadata.json."""
    legacy = tmp_path / "issue1"
    legacy.mkdir()
    (legacy / "metadata.json").write_text(json.dumps({"session_builder": "L"}))
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / ROLE_SESSIONS_FILENAME).write_text(
        json.dumps({"version": 1, "sessions": {"builder": "J"}})
    )
    store = RoleSessionStore(
        seed_id="seed",
        seed_run_dir=seed,
        legacy_self_run_dir=legacy,
    )
    assert store.get("builder") == "J"


def test_beads_overrides_json_and_legacy(fake_bd: _FakeBd, tmp_path: Path) -> None:
    """BeadsStore tier wins outright."""
    legacy = tmp_path / "issue1"
    legacy.mkdir()
    (legacy / "metadata.json").write_text(json.dumps({"session_builder": "L"}))
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / ROLE_SESSIONS_FILENAME).write_text(
        json.dumps({"version": 1, "sessions": {"builder": "J"}})
    )
    fake_bd.shows = {"seed": {"id": "seed", "metadata": {"session_builder": "B"}}}
    store = RoleSessionStore(
        seed_id="seed",
        seed_run_dir=seed,
        legacy_self_run_dir=legacy,
    )
    assert store.get("builder") == "B"


# ─────────────────────── set semantics ───────────────────────────────


def test_set_writes_to_beads_when_seed_exists(fake_bd: _FakeBd, tmp_path: Path) -> None:
    fake_bd.shows = {"seed": {"id": "seed", "metadata": {}}}
    store = RoleSessionStore(seed_id="seed", seed_run_dir=tmp_path / "seed")
    store.set("builder", "uuid-1")
    # Must have shelled out to `bd update seed --set-metadata session_builder=uuid-1`.
    found = any(
        cmd[:3] == ["bd", "update", "seed"]
        and "--set-metadata" in cmd
        and "session_builder=uuid-1" in cmd
        for cmd in fake_bd.updates
    )
    assert found, fake_bd.updates


def test_set_falls_back_to_json_when_no_bd(no_bd: None, tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    store = RoleSessionStore(seed_id="seed", seed_run_dir=seed)
    store.set("builder", "uuid-2")
    on_disk = json.loads((seed / ROLE_SESSIONS_FILENAME).read_text())
    assert on_disk == {"version": 1, "sessions": {"builder": "uuid-2"}}


def test_set_falls_back_to_json_when_seed_bead_missing(
    fake_bd: _FakeBd, tmp_path: Path
) -> None:
    """bd is on PATH but seed bead doesn't exist → fall back to JSON file."""
    # No fake_bd.shows entry for "ghost-seed" → _bd_show returns None.
    seed = tmp_path / "ghost-seed"
    store = RoleSessionStore(seed_id="ghost-seed", seed_run_dir=seed)
    store.set("builder", "uuid-3")
    on_disk = json.loads((seed / ROLE_SESSIONS_FILENAME).read_text())
    assert on_disk["sessions"] == {"builder": "uuid-3"}


def test_set_after_legacy_hit_does_not_mutate_legacy_file(
    fake_bd: _FakeBd, tmp_path: Path
) -> None:
    """AC (c) follow-up: writes go forward; archived legacy is preserved."""
    legacy = tmp_path / "issue1"
    legacy.mkdir()
    legacy_path = legacy / "metadata.json"
    legacy_payload = {"session_builder": "legacy-uuid"}
    legacy_path.write_text(json.dumps(legacy_payload))
    fake_bd.shows = {"seed": {"id": "seed", "metadata": {}}}
    store = RoleSessionStore(
        seed_id="seed",
        seed_run_dir=tmp_path / "seed",
        legacy_self_run_dir=legacy,
    )
    # First read finds legacy.
    assert store.get("builder") == "legacy-uuid"
    # Now write forward.
    store.set("builder", "new-uuid")
    # Legacy file is untouched.
    assert json.loads(legacy_path.read_text()) == legacy_payload


# ─────────────────────── all() unioning ──────────────────────────────


def test_all_unions_with_beads_winning(fake_bd: _FakeBd, tmp_path: Path) -> None:
    legacy = tmp_path / "issue1"
    legacy.mkdir()
    (legacy / "metadata.json").write_text(
        json.dumps({"session_only_legacy": "L", "session_shared": "L-shared"})
    )
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / ROLE_SESSIONS_FILENAME).write_text(
        json.dumps(
            {
                "version": 1,
                "sessions": {"only_json": "J", "shared": "J-shared"},
            }
        )
    )
    fake_bd.shows = {
        "seed": {
            "id": "seed",
            "metadata": {"session_only_beads": "B", "session_shared": "B-shared"},
        }
    }
    store = RoleSessionStore(
        seed_id="seed",
        seed_run_dir=seed,
        legacy_self_run_dir=legacy,
    )
    out = store.all()
    assert out["only_legacy"] == "L"
    assert out["only_json"] == "J"
    assert out["only_beads"] == "B"
    # Beads wins on overlap.
    assert out["shared"] == "B-shared"


# ─────────────────────── atomic write ────────────────────────────────


def test_write_json_is_atomic_rename(no_bd: None, tmp_path: Path) -> None:
    """Tempfile is created in the same dir + os.replace into target."""
    seed = tmp_path / "seed"
    store = RoleSessionStore(seed_id="seed", seed_run_dir=seed)
    store.set("a", "1")
    # Tempfile must not survive the rename.
    leftovers = list(seed.glob(".role-sessions.*.tmp"))
    assert leftovers == []
    # Final file is well-formed JSON with version + sessions.
    on_disk = json.loads((seed / ROLE_SESSIONS_FILENAME).read_text())
    assert on_disk["version"] == 1
    assert on_disk["sessions"] == {"a": "1"}


def test_write_json_handles_pre_existing_file(no_bd: None, tmp_path: Path) -> None:
    """Subsequent set() preserves the prior session entries."""
    seed = tmp_path / "seed"
    store = RoleSessionStore(seed_id="seed", seed_run_dir=seed)
    store.set("a", "1")
    store.set("b", "2")
    on_disk = json.loads((seed / ROLE_SESSIONS_FILENAME).read_text())
    assert on_disk["sessions"] == {"a": "1", "b": "2"}


def test_read_json_tolerates_corrupt_file(no_bd: None, tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / ROLE_SESSIONS_FILENAME).write_text("not json {{{")
    store = RoleSessionStore(seed_id="seed", seed_run_dir=seed)
    # Treat as empty; do not raise.
    assert store.get("a") is None


# ─────────────────────── module-level guard ──────────────────────────


def test_module_exports() -> None:
    """Sanity: public symbols are exposed for callers."""
    assert role_sessions.RoleSessionStore is RoleSessionStore
    assert isinstance(role_sessions.SESSION_PREFIX, str)
