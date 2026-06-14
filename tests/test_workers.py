"""Unit tests for prefect_orchestration.workers — the ensure_pool_worker guard.

No real Prefect server and no real worker process: the API probe and the
detached spawn are both seams the tests monkeypatch.
"""

from __future__ import annotations

import pytest

from prefect_orchestration import workers


# ---- auto_worker_enabled toggle --------------------------------------------


def test_auto_worker_enabled_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(workers.AUTO_WORKER_ENV, raising=False)
    assert workers.auto_worker_enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "FALSE", "no", "off", "Off"])
def test_auto_worker_disabled_values(monkeypatch: pytest.MonkeyPatch, val: str) -> None:
    monkeypatch.setenv(workers.AUTO_WORKER_ENV, val)
    assert workers.auto_worker_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "yes", "on", ""])
def test_auto_worker_enabled_truthy_values(
    monkeypatch: pytest.MonkeyPatch, val: str
) -> None:
    monkeypatch.setenv(workers.AUTO_WORKER_ENV, val)
    assert workers.auto_worker_enabled() is True


# ---- ensure_pool_worker: disabled short-circuit ----------------------------


def test_ensure_disabled_does_not_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(workers.AUTO_WORKER_ENV, "0")
    spawned: list = []
    monkeypatch.setattr(
        workers, "spawn_detached_worker", lambda *a, **k: spawned.append(1)
    )
    result = workers.ensure_pool_worker("po")
    assert result.action == "disabled"
    assert not result.spawned
    assert spawned == []


# ---- ensure_pool_worker: already-online (no-op, idempotent) ----------------


def test_ensure_already_online_via_supplied_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned: list = []
    monkeypatch.setattr(
        workers, "spawn_detached_worker", lambda *a, **k: spawned.append(1)
    )
    # online_count supplied → no API probe, no spawn.
    result = workers.ensure_pool_worker("po", online_count=2)
    assert result.action == "already-online"
    assert spawned == []


def test_ensure_already_online_via_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(workers.AUTO_WORKER_ENV, raising=False)
    monkeypatch.setattr(workers, "count_online_workers", lambda pool, **k: 1)
    spawned: list = []
    monkeypatch.setattr(
        workers, "spawn_detached_worker", lambda *a, **k: spawned.append(1)
    )
    result = workers.ensure_pool_worker("po")
    assert result.action == "already-online"
    assert spawned == []


# ---- ensure_pool_worker: local process dedup -------------------------------


def test_ensure_skips_when_local_process_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(workers.AUTO_WORKER_ENV, raising=False)
    monkeypatch.setattr(workers, "local_worker_process_running", lambda pool: True)
    spawned: list = []
    monkeypatch.setattr(
        workers, "spawn_detached_worker", lambda *a, **k: spawned.append(1)
    )
    result = workers.ensure_pool_worker("po", online_count=0)
    assert result.action == "already-online"
    assert spawned == []


# ---- ensure_pool_worker: spawn path ----------------------------------------


def test_ensure_spawns_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(workers.AUTO_WORKER_ENV, raising=False)
    monkeypatch.setattr(workers, "local_worker_process_running", lambda pool: False)
    calls: list = []

    def fake_spawn(pool: str, *, pool_type: str = "process") -> int:
        calls.append((pool, pool_type))
        return 4242

    monkeypatch.setattr(workers, "spawn_detached_worker", fake_spawn)
    result = workers.ensure_pool_worker("po", online_count=0, quiet=True)
    assert result.action == "spawned"
    assert result.spawned is True
    assert result.pid == 4242
    assert calls == [("po", "process")]


def test_ensure_unreachable_when_probe_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(workers.AUTO_WORKER_ENV, raising=False)
    monkeypatch.setattr(workers, "count_online_workers", lambda pool, **k: None)
    spawned: list = []
    monkeypatch.setattr(
        workers, "spawn_detached_worker", lambda *a, **k: spawned.append(1)
    )
    result = workers.ensure_pool_worker("po")
    assert result.action == "unreachable"
    assert spawned == []


def test_ensure_spawn_failure_returns_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(workers.AUTO_WORKER_ENV, raising=False)
    monkeypatch.setattr(workers, "local_worker_process_running", lambda pool: False)

    def boom(pool: str, *, pool_type: str = "process") -> int:
        raise FileNotFoundError("`prefect` not on PATH; cannot spawn a worker")

    monkeypatch.setattr(workers, "spawn_detached_worker", boom)
    result = workers.ensure_pool_worker("po", online_count=0)
    assert result.action == "failed"
    assert "prefect" in result.message
    assert not result.spawned


# ---- local_worker_process_running token matching ---------------------------


def test_local_worker_process_running_matches_exact_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    monkeypatch.setattr(workers.shutil, "which", lambda b: "/usr/bin/pgrep")

    def fake_run(args, **kw):
        out = "111 prefect worker start --pool po --type process\n"
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    monkeypatch.setattr(workers.subprocess, "run", fake_run)
    assert workers.local_worker_process_running("po") is True
    # A different pool name must NOT match the `po` line.
    assert workers.local_worker_process_running("other") is False


def test_local_worker_process_running_matches_equals_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    monkeypatch.setattr(workers.shutil, "which", lambda b: "/usr/bin/pgrep")

    def fake_run(args, **kw):
        return subprocess.CompletedProcess(
            args, 0, stdout="222 prefect worker start --pool=po\n", stderr=""
        )

    monkeypatch.setattr(workers.subprocess, "run", fake_run)
    assert workers.local_worker_process_running("po") is True


def test_local_worker_process_running_no_pgrep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workers.shutil, "which", lambda b: None)
    assert workers.local_worker_process_running("po") is False


# ---- spawn_detached_worker raises without prefect --------------------------


def test_spawn_detached_worker_requires_prefect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workers.shutil, "which", lambda b: None)
    with pytest.raises(FileNotFoundError):
        workers.spawn_detached_worker("po")
