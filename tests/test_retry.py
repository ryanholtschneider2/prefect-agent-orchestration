"""Unit tests for prefect_orchestration.retry."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from prefect_orchestration import retry, run_lookup


# ─── helpers ──────────────────────────────────────────────────────────


class _BdFake:
    """Stand-in for subprocess.run capturing `bd` invocations."""

    def __init__(self, status: str = "open") -> None:
        self.status = status
        self.calls: list[list[str]] = []

    def __call__(self, cmd, capture_output=True, text=True, check=False):  # noqa: ARG002
        self.calls.append(list(cmd))
        if len(cmd) >= 2 and cmd[1] == "show":
            payload = {"id": cmd[2], "status": self.status}
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(payload), stderr=""
            )
        # `bd update ...` — pretend it succeeded.
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def _seed_run(
    tmp_path: Path,
    *,
    with_metadata: bool = False,
    formula_stamp: str | None = None,
) -> tuple[Path, Path]:
    rig_path = tmp_path / "rig"
    run_dir = rig_path / ".planning" / "software-dev-full" / "beads-xyz"
    run_dir.mkdir(parents=True)
    (run_dir / "triage.md").write_text("prior triage")
    if with_metadata:
        (run_dir / "metadata.json").write_text(
            json.dumps({"sessions": {"builder": "uuid-1", "critic": "uuid-2"}})
        )
    if formula_stamp is not None:
        (run_dir / retry.FORMULA_STAMP).write_text(formula_stamp)
    return rig_path, run_dir


def _patch_resolve(monkeypatch, rig_path: Path, run_dir: Path) -> None:
    def _fake_resolve(_issue_id: str) -> run_lookup.RunLocation:
        return run_lookup.RunLocation(rig_path=rig_path, run_dir=run_dir)

    monkeypatch.setattr(retry.run_lookup, "resolve_run_dir", _fake_resolve)


class _FormulaSpy:
    def __init__(self, result: Any = "flow-ok") -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = result

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.result


def _patch_formula(
    monkeypatch, spy: _FormulaSpy, *, name: str = "software-dev-full"
) -> None:
    def _load(n: str):
        assert n == name
        return spy

    monkeypatch.setattr(retry, "_load_formula", _load)
    # Ensure the Prefect fallback resolves to `name` when no stamp file exists,
    # so tests that don't seed a stamp still reach _load_formula.
    monkeypatch.setattr(retry, "_formula_from_prefect", lambda _id: name)


# ─── AC1: archive ─────────────────────────────────────────────────────


def test_run_dir_archived_with_timestamp(tmp_path, monkeypatch):
    rig_path, run_dir = _seed_run(tmp_path)
    _patch_resolve(monkeypatch, rig_path, run_dir)
    bd = _BdFake(status="open")
    monkeypatch.setattr(retry.subprocess, "run", bd)
    spy = _FormulaSpy()
    _patch_formula(monkeypatch, spy)

    result = retry.retry_issue("beads-xyz", _in_flight_probe=lambda _i: 0)

    assert not run_dir.exists()
    siblings = [
        p for p in run_dir.parent.iterdir() if p.name.startswith(run_dir.name + ".bak-")
    ]
    assert len(siblings) == 1
    assert (siblings[0] / "triage.md").read_text() == "prior triage"
    assert result.archived_to == siblings[0]
    assert result.launched is True


# ─── AC2: reopen closed beads, don't churn open ones ──────────────────


def test_closed_bead_is_reopened(tmp_path, monkeypatch):
    rig_path, run_dir = _seed_run(tmp_path)
    _patch_resolve(monkeypatch, rig_path, run_dir)
    bd = _BdFake(status="closed")
    monkeypatch.setattr(retry.subprocess, "run", bd)
    monkeypatch.setattr(retry.shutil, "which", lambda _n: "/usr/bin/bd")
    _patch_formula(monkeypatch, _FormulaSpy())

    result = retry.retry_issue("beads-xyz", _in_flight_probe=lambda _i: 0)

    assert result.reopened is True
    update_calls = [c for c in bd.calls if len(c) >= 2 and c[1] == "update"]
    assert update_calls, "expected a `bd update` call"
    update = update_calls[0]
    assert "--status" in update and "open" in update
    assert "--assignee" in update


def test_open_bead_is_not_touched(tmp_path, monkeypatch):
    rig_path, run_dir = _seed_run(tmp_path)
    _patch_resolve(monkeypatch, rig_path, run_dir)
    bd = _BdFake(status="open")
    monkeypatch.setattr(retry.subprocess, "run", bd)
    monkeypatch.setattr(retry.shutil, "which", lambda _n: "/usr/bin/bd")
    _patch_formula(monkeypatch, _FormulaSpy())

    result = retry.retry_issue("beads-xyz", _in_flight_probe=lambda _i: 0)

    assert result.reopened is False
    assert not any(c[1] == "update" for c in bd.calls if len(c) >= 2)


# ─── AC3: flow is invoked after archive + reopen ──────────────────────


def test_flow_called_with_issue_and_rig(tmp_path, monkeypatch):
    rig_path, run_dir = _seed_run(tmp_path)
    _patch_resolve(monkeypatch, rig_path, run_dir)
    monkeypatch.setattr(retry.subprocess, "run", _BdFake(status="open"))
    monkeypatch.setattr(retry.shutil, "which", lambda _n: "/usr/bin/bd")
    spy = _FormulaSpy(result="done")
    _patch_formula(monkeypatch, spy)

    result = retry.retry_issue("beads-xyz", _in_flight_probe=lambda _i: 0)

    assert len(spy.calls) == 1
    kw = spy.calls[0]
    assert kw["issue_id"] == "beads-xyz"
    assert kw["rig"] == rig_path.name
    assert kw["rig_path"] == str(rig_path)
    assert result.flow_result == "done"


def test_rig_override_wins(tmp_path, monkeypatch):
    rig_path, run_dir = _seed_run(tmp_path)
    _patch_resolve(monkeypatch, rig_path, run_dir)
    monkeypatch.setattr(retry.subprocess, "run", _BdFake(status="open"))
    monkeypatch.setattr(retry.shutil, "which", lambda _n: "/usr/bin/bd")
    spy = _FormulaSpy()
    _patch_formula(monkeypatch, spy)

    retry.retry_issue("beads-xyz", rig="custom-rig", _in_flight_probe=lambda _i: 0)

    assert spy.calls[0]["rig"] == "custom-rig"


# ─── AC4: keep-sessions preserves metadata.json ───────────────────────


def test_keep_sessions_restores_metadata(tmp_path, monkeypatch):
    rig_path, run_dir = _seed_run(tmp_path, with_metadata=True)
    original_bytes = (run_dir / "metadata.json").read_bytes()
    _patch_resolve(monkeypatch, rig_path, run_dir)
    monkeypatch.setattr(retry.subprocess, "run", _BdFake(status="open"))
    monkeypatch.setattr(retry.shutil, "which", lambda _n: "/usr/bin/bd")
    _patch_formula(monkeypatch, _FormulaSpy())

    result = retry.retry_issue(
        "beads-xyz", keep_sessions=True, _in_flight_probe=lambda _i: 0
    )

    new_meta = run_dir / "metadata.json"
    assert new_meta.exists()
    assert new_meta.read_bytes() == original_bytes
    assert result.kept_sessions is True
    # archive also has the metadata (it was renamed wholesale)
    archived = result.archived_to
    assert archived is not None
    assert (archived / "metadata.json").read_bytes() == original_bytes


def test_default_does_not_restore_metadata(tmp_path, monkeypatch):
    rig_path, run_dir = _seed_run(tmp_path, with_metadata=True)
    _patch_resolve(monkeypatch, rig_path, run_dir)
    monkeypatch.setattr(retry.subprocess, "run", _BdFake(status="open"))
    monkeypatch.setattr(retry.shutil, "which", lambda _n: "/usr/bin/bd")
    _patch_formula(monkeypatch, _FormulaSpy())

    result = retry.retry_issue("beads-xyz", _in_flight_probe=lambda _i: 0)

    assert not run_dir.exists() or not (run_dir / "metadata.json").exists()
    assert result.kept_sessions is False


def test_keep_sessions_warns_when_metadata_missing(tmp_path, monkeypatch):
    rig_path, run_dir = _seed_run(tmp_path, with_metadata=False)
    _patch_resolve(monkeypatch, rig_path, run_dir)
    monkeypatch.setattr(retry.subprocess, "run", _BdFake(status="open"))
    monkeypatch.setattr(retry.shutil, "which", lambda _n: "/usr/bin/bd")
    _patch_formula(monkeypatch, _FormulaSpy())

    warnings: list[str] = []
    retry.retry_issue(
        "beads-xyz",
        keep_sessions=True,
        warn=warnings.append,
        _in_flight_probe=lambda _i: 0,
    )
    assert any("metadata.json" in w for w in warnings)


# ─── AC5: in-flight guard ─────────────────────────────────────────────


def test_in_flight_run_refuses(tmp_path, monkeypatch):
    rig_path, run_dir = _seed_run(tmp_path)
    _patch_resolve(monkeypatch, rig_path, run_dir)
    monkeypatch.setattr(retry.subprocess, "run", _BdFake(status="open"))
    spy = _FormulaSpy()
    _patch_formula(monkeypatch, spy)

    with pytest.raises(retry.RetryError) as exc:
        retry.retry_issue("beads-xyz", _in_flight_probe=lambda _i: 1)

    assert exc.value.exit_code == 3
    # No archive, no launch.
    assert run_dir.exists()
    siblings = [
        p for p in run_dir.parent.iterdir() if p.name.startswith(run_dir.name + ".bak-")
    ]
    assert siblings == []
    assert spy.calls == []


def test_force_bypasses_in_flight_check(tmp_path, monkeypatch):
    rig_path, run_dir = _seed_run(tmp_path)
    _patch_resolve(monkeypatch, rig_path, run_dir)
    monkeypatch.setattr(retry.subprocess, "run", _BdFake(status="open"))
    monkeypatch.setattr(retry.shutil, "which", lambda _n: "/usr/bin/bd")
    spy = _FormulaSpy()
    _patch_formula(monkeypatch, spy)

    # Probe would have said "1 running", but force=True should skip the probe.
    def _probe_should_not_be_called(_i: str) -> int:
        raise AssertionError("probe called despite --force")

    result = retry.retry_issue(
        "beads-xyz", force=True, _in_flight_probe=_probe_should_not_be_called
    )
    assert result.launched is True
    assert len(spy.calls) == 1


# ─── concurrent retry lock ────────────────────────────────────────────


def test_concurrent_retry_exits_three(tmp_path, monkeypatch):
    rig_path, run_dir = _seed_run(tmp_path)
    _patch_resolve(monkeypatch, rig_path, run_dir)
    monkeypatch.setattr(retry.subprocess, "run", _BdFake(status="open"))
    monkeypatch.setattr(retry.shutil, "which", lambda _n: "/usr/bin/bd")
    _patch_formula(monkeypatch, _FormulaSpy())

    import fcntl
    import os

    lock_path = run_dir.with_name(run_dir.name + retry.LOCK_SUFFIX)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(retry.RetryError) as exc:
            retry.retry_issue("beads-xyz", _in_flight_probe=lambda _i: 0)
        assert exc.value.exit_code == 3
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# ─── missing run_dir surfaces RunDirNotFound ──────────────────────────


def test_missing_metadata_surfaces_run_dir_not_found(tmp_path, monkeypatch):
    def _raise(_i: str):
        raise run_lookup.RunDirNotFound("no run_dir recorded")

    monkeypatch.setattr(retry.run_lookup, "resolve_run_dir", _raise)
    with pytest.raises(run_lookup.RunDirNotFound):
        retry.retry_issue("beads-xyz", _in_flight_probe=lambda _i: 0)


# ─── formula not installed ────────────────────────────────────────────


def test_unknown_formula_exit_four(tmp_path, monkeypatch):
    rig_path, run_dir = _seed_run(tmp_path)
    _patch_resolve(monkeypatch, rig_path, run_dir)
    monkeypatch.setattr(retry.subprocess, "run", _BdFake(status="open"))
    monkeypatch.setattr(retry.shutil, "which", lambda _n: "/usr/bin/bd")

    def _load(_n: str):
        raise retry.RetryError("no formula named 'nope'", exit_code=4)

    monkeypatch.setattr(retry, "_load_formula", _load)

    with pytest.raises(retry.RetryError) as exc:
        retry.retry_issue("beads-xyz", formula="nope", _in_flight_probe=lambda _i: 0)
    assert exc.value.exit_code == 4


# ─── sav.3: tmux pre-cleanup before relaunch ──────────────────────────


def test_retry_kills_prior_tmux_for_issue(tmp_path, monkeypatch):
    """Before archiving, retry asks tmux_tracker to kill any prior session."""
    from prefect_orchestration import tmux_tracker

    rig_path, run_dir = _seed_run(tmp_path)
    _patch_resolve(monkeypatch, rig_path, run_dir)
    monkeypatch.setattr(retry.subprocess, "run", _BdFake(status="open"))
    spy = _FormulaSpy()
    _patch_formula(monkeypatch, spy)

    calls: list[str] = []
    monkeypatch.setattr(
        tmux_tracker, "kill_for_issue", lambda iid: calls.append(iid) or 0
    )

    retry.retry_issue("beads-xyz", _in_flight_probe=lambda _i: 0)

    assert calls == ["beads-xyz"]


# ─── formula resolution chain ─────────────────────────────────────────


def test_explicit_formula_overrides_stamp(tmp_path, monkeypatch):
    """Explicit --formula wins even when a stamp file is present."""
    rig_path, run_dir = _seed_run(tmp_path, formula_stamp="software-dev-fast")
    _patch_resolve(monkeypatch, rig_path, run_dir)
    monkeypatch.setattr(retry.subprocess, "run", _BdFake(status="open"))
    spy = _FormulaSpy()
    _patch_formula(monkeypatch, spy, name="software-dev-full")

    retry.retry_issue(
        "beads-xyz",
        formula="software-dev-full",
        _in_flight_probe=lambda _i: 0,
    )

    assert spy.calls[0]["issue_id"] == "beads-xyz"


def test_stamp_file_used_when_present(tmp_path, monkeypatch):
    """Stamp file is honoured when no explicit --formula is given."""
    rig_path, run_dir = _seed_run(tmp_path, formula_stamp="software-dev-fast")
    _patch_resolve(monkeypatch, rig_path, run_dir)
    monkeypatch.setattr(retry.subprocess, "run", _BdFake(status="open"))
    spy = _FormulaSpy()
    _patch_formula(monkeypatch, spy, name="software-dev-fast")

    retry.retry_issue("beads-xyz", _in_flight_probe=lambda _i: 0)

    assert spy.calls[0]["issue_id"] == "beads-xyz"


def test_prefect_fallback_used_when_no_stamp(tmp_path, monkeypatch):
    """No stamp → Prefect fallback is used and a warning is emitted."""
    rig_path, run_dir = _seed_run(tmp_path)
    _patch_resolve(monkeypatch, rig_path, run_dir)
    monkeypatch.setattr(retry.subprocess, "run", _BdFake(status="open"))
    monkeypatch.setattr(retry, "_formula_from_prefect", lambda _id: "software-dev-fast")
    spy = _FormulaSpy()
    _patch_formula(monkeypatch, spy, name="software-dev-fast")

    warnings: list[str] = []
    retry.retry_issue("beads-xyz", warn=warnings.append, _in_flight_probe=lambda _i: 0)

    assert any("Prefect history" in w for w in warnings)
    assert spy.calls[0]["issue_id"] == "beads-xyz"


def test_no_formula_found_raises_exit_four(tmp_path, monkeypatch):
    """No stamp + no Prefect history → RetryError with exit_code=4."""
    rig_path, run_dir = _seed_run(tmp_path)
    _patch_resolve(monkeypatch, rig_path, run_dir)
    monkeypatch.setattr(retry.subprocess, "run", _BdFake(status="open"))
    monkeypatch.setattr(retry, "_formula_from_prefect", lambda _id: None)

    with pytest.raises(retry.RetryError) as exc:
        retry.retry_issue("beads-xyz", _in_flight_probe=lambda _i: 0)

    assert exc.value.exit_code == 4
    assert "po list" in str(exc.value)


def test_retry_tmux_cleanup_failure_is_nonfatal(tmp_path, monkeypatch):
    """A raised exception from kill_for_issue must not abort retry."""
    from prefect_orchestration import tmux_tracker

    rig_path, run_dir = _seed_run(tmp_path)
    _patch_resolve(monkeypatch, rig_path, run_dir)
    monkeypatch.setattr(retry.subprocess, "run", _BdFake(status="open"))
    spy = _FormulaSpy()
    _patch_formula(monkeypatch, spy)

    def _raise(_iid: str) -> int:
        raise RuntimeError("tmux blew up")

    monkeypatch.setattr(tmux_tracker, "kill_for_issue", _raise)

    warnings: list[str] = []
    result = retry.retry_issue(
        "beads-xyz",
        _in_flight_probe=lambda _i: 0,
        warn=warnings.append,
    )
    assert result.launched is True
    assert any("tmux pre-cleanup" in w for w in warnings)
