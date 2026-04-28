"""Per-seed-bead role→session-uuid persistence.

Today (`role_registry.RoleRegistry.persist`) writes `session_<role>` keys
through a `MetadataStore` keyed by either the issue itself
(`FileStore(<run_dir>/metadata.json)`) or an explicit `parent_bead`
(`BeadsStore`). That couples session lifetime to one bead — fine for a
single solo run, but breaks role affinity across child beads of the
same parent (build.iter1 → build.iter2, …).

This module layers a **seed-bead-keyed** view on top: callers point a
`RoleSessionStore` at the topmost parent-child ancestor of the active
issue (resolved via `beads_meta.resolve_seed_bead`), and reads/writes
flow through the seed so all sibling children share the same map.

Three on-disk tiers, in lookup precedence order:

1. **`BeadsStore(seed_id)`** — `bd show <seed>.metadata.session_<role>`.
   Primary write tier when `bd` is on PATH and the seed exists. Visible
   via `bd show`; integrates with today's epic/graph BeadsStore
   behavior (no migration needed for those).
2. **`<seed_run_dir>/role-sessions.json`** — `{version, sessions:{role:uuid}}`.
   Offline fallback when `bd` is unavailable or the seed bead doesn't
   exist as a tracked bead. Atomic write (tempfile + rename).
3. **Legacy shim:** `<self_run_dir>/metadata.json` `session_<role>`.
   Read-only; enables seamless migration of old solo-run artifacts.
   Subsequent `set()` writes forward to (1) or (2); legacy file is
   never mutated (preserves archived forensic state).

Internal API uses bare-role keys (`role`); conversion to the
`session_<role>` prefix happens at the `sessions.py` boundary.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from prefect_orchestration.beads_meta import (
    BeadsStore,
    _bd_available,
    _bd_show,
)

ROLE_SESSIONS_FILENAME = "role-sessions.json"
ROLE_SESSIONS_VERSION = 1
SESSION_PREFIX = "session_"


@dataclass
class RoleSessionStore:
    """Seed-bead-keyed role→uuid persistence with three-tier lookup.

    Parameters
    ----------
    seed_id
        Bead id to scope reads/writes against. Often the topmost
        parent-child ancestor of the active issue (see
        `beads_meta.resolve_seed_bead`); for solo runs it is the issue
        itself, in which case behavior matches today's per-bead model.
    seed_run_dir
        `<rig_path>/.planning/<formula>/<seed_id>/`. Created on first
        write if absent.
    rig_path
        Where bd should resolve its `.beads/` from (passed through to
        `BeadsStore` shellouts).
    legacy_self_run_dir
        The *issue's own* run-dir, distinct from `seed_run_dir` when
        `seed_id != issue_id`. Read-only migration shim source.
    """

    seed_id: str
    seed_run_dir: Path
    rig_path: Path | str | None = None
    legacy_self_run_dir: Path | None = None

    # ─── path/store helpers ──────────────────────────────────────────

    @property
    def _json_path(self) -> Path:
        return self.seed_run_dir / ROLE_SESSIONS_FILENAME

    def _read_json(self) -> dict[str, str]:
        path = self._json_path
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        sessions = data.get("sessions")
        if not isinstance(sessions, dict):
            return {}
        # Coerce values to str defensively.
        return {str(k): str(v) for k, v in sessions.items() if v is not None}

    def _write_json(self, sessions: dict[str, str]) -> None:
        self.seed_run_dir.mkdir(parents=True, exist_ok=True)
        payload = {"version": ROLE_SESSIONS_VERSION, "sessions": dict(sessions)}
        # Atomic write: tempfile in same dir + os.replace.
        target = self._json_path
        # NamedTemporaryFile lets us control delete-on-close so we can
        # rename it into place.
        fd, tmp_name = tempfile.mkstemp(
            prefix=".role-sessions.", suffix=".json.tmp", dir=str(self.seed_run_dir)
        )
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, target)
        except Exception:
            # Best-effort cleanup; re-raise so caller sees the failure.
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _read_beads(self) -> dict[str, str]:
        if not _bd_available() or not self.seed_id:
            return {}
        try:
            row = _bd_show(self.seed_id, rig_path=self.rig_path)
        except Exception:  # noqa: BLE001
            return {}
        if not isinstance(row, dict):
            return {}
        meta = row.get("metadata") or {}
        if not isinstance(meta, dict):
            return {}
        out: dict[str, str] = {}
        for k, v in meta.items():
            if isinstance(k, str) and k.startswith(SESSION_PREFIX) and v is not None:
                out[k[len(SESSION_PREFIX) :]] = str(v)
        return out

    def _read_legacy(self) -> dict[str, str]:
        """Legacy `<self_run_dir>/metadata.json` `session_<role>` keys."""
        if self.legacy_self_run_dir is None:
            return {}
        path = self.legacy_self_run_dir / "metadata.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        out: dict[str, str] = {}
        for k, v in data.items():
            if isinstance(k, str) and k.startswith(SESSION_PREFIX) and v is not None:
                out[k[len(SESSION_PREFIX) :]] = str(v)
        return out

    def _seed_bead_exists(self) -> bool:
        if not _bd_available() or not self.seed_id:
            return False
        try:
            return _bd_show(self.seed_id, rig_path=self.rig_path) is not None
        except Exception:  # noqa: BLE001
            return False

    # ─── public API ──────────────────────────────────────────────────

    def get(self, role: str) -> str | None:
        """Return the session uuid for `role`, or None if not recorded.

        Lookup tiers: BeadsStore → role-sessions.json → legacy shim.
        First non-empty hit wins; absent everywhere returns None.
        """
        beads = self._read_beads()
        if role in beads:
            return beads[role]
        local = self._read_json()
        if role in local:
            return local[role]
        legacy = self._read_legacy()
        return legacy.get(role)

    def set(self, role: str, uuid: str) -> None:
        """Persist `(role, uuid)` to the highest-precedence available tier.

        - If `bd` is on PATH and the seed bead exists → write through
          `BeadsStore(seed_id)`. Single source of truth, visible via
          `bd show`.
        - Otherwise → write to `<seed_run_dir>/role-sessions.json`.

        Legacy `<self_run_dir>/metadata.json` is **never** mutated by
        `set` (read-only migration shim; preserves archived forensic
        state).
        """
        if self._seed_bead_exists():
            BeadsStore(parent_id=self.seed_id, rig_path=self.rig_path).set(
                f"{SESSION_PREFIX}{role}", uuid
            )
            return
        sessions = self._read_json()
        sessions[role] = uuid
        self._write_json(sessions)

    def all(self) -> dict[str, str]:
        """Unioned `{role: uuid}` map across all three tiers.

        Precedence (last-wins) on overlap: legacy < json < beads. This
        ensures a value freshly written via `set` (BeadsStore tier)
        shadows any stale legacy/json copy.
        """
        out = self._read_legacy()
        out.update(self._read_json())
        out.update(self._read_beads())
        return out


__all__ = [
    "RoleSessionStore",
    "ROLE_SESSIONS_FILENAME",
    "ROLE_SESSIONS_VERSION",
    "SESSION_PREFIX",
]
