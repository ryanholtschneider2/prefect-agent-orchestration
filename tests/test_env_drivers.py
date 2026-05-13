"""Unit tests for `prefect_orchestration.env_drivers`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from prefect_orchestration import env_drivers as ed
from prefect_orchestration.env_drivers import (
    EnvDriver,
    EnvHandle,
    EnvHealth,
    NoopDriver,
    list_driver_eps,
    load_drivers,
)


@dataclass
class _FakeEP:
    name: str
    target: Any = None
    raises: Exception | None = None

    def load(self) -> Any:
        if self.raises is not None:
            raise self.raises
        return self.target


# -- EnvHandle ---------------------------------------------------------


def test_envhandle_rejects_non_json_values():
    with pytest.raises(TypeError):
        EnvHandle(driver_name="d", opaque={"p": Path("/tmp")})


def test_envhandle_rejects_non_str_keys():
    with pytest.raises(TypeError):
        EnvHandle(driver_name="d", opaque={1: "v"})  # type: ignore[dict-item]


def test_envhandle_accepts_nested_json():
    h = EnvHandle(
        driver_name="d",
        opaque={"a": 1, "b": [1, 2, "x"], "c": {"nested": [True, None]}},
    )
    assert h.driver_name == "d"
    assert h.opaque["b"] == [1, 2, "x"]


def test_envhandle_default_opaque_empty():
    h = EnvHandle(driver_name="d")
    assert h.opaque == {}


# -- EnvHealth ---------------------------------------------------------


def test_envhealth_shape():
    h = EnvHealth(ok=True, summary="reachable", details={"reachable": True})
    assert h.ok is True
    assert h.summary == "reachable"
    assert h.details == {"reachable": True}


# -- NoopDriver / Protocol --------------------------------------------


def test_noop_driver_implements_protocol():
    # `runtime_checkable` validates method presence only; `name: str` and
    # type annotations are not enforced. The full-method-sweep test below
    # is the real coverage.
    assert isinstance(NoopDriver(), EnvDriver)


def test_noop_driver_full_method_sweep(tmp_path: Path):
    d = NoopDriver()
    h = d.provision("e1", "snap-abc", {"region": "us-east"})
    assert isinstance(h, EnvHandle)
    assert h.driver_name == "noop"
    assert h.opaque == {"name": "e1", "snapshot_tag": "snap-abc"}

    argv = d.attach_argv(h, "builder", "9ws_2")
    assert argv == ["true"]

    d.push_identity(h, tmp_path / "id.tar", "hash123")

    d.push_credentials(h, {"GITHUB_TOKEN": "ghp_x"}, b"creds-bytes")

    remote = d.ensure_rig_remote(h)
    assert remote == ""

    d.start_worker(h, "po-env-e1")

    health = d.health(h)
    assert isinstance(health, EnvHealth)
    assert health.ok is True
    assert health.summary == "noop healthy"

    d.teardown(h)

    methods_called = [c[0] for c in d.calls]
    assert methods_called == [
        "provision",
        "attach_argv",
        "push_identity",
        "push_credentials",
        "ensure_rig_remote",
        "start_worker",
        "health",
        "teardown",
    ]


def test_noop_driver_push_credentials_handles_none():
    d = NoopDriver()
    h = d.provision("e", "snap", None)
    d.push_credentials(h, {}, None)
    last = d.calls[-1]
    assert last[0] == "push_credentials"
    assert last[2] == 0  # bytes-len when None


# -- load_drivers -----------------------------------------------------


def test_load_drivers_empty_when_no_eps_registered(monkeypatch):
    monkeypatch.setattr(ed, "entry_points", lambda group=None, **_: [])
    assert load_drivers() == {}


def test_load_drivers_skips_broken_target(monkeypatch):
    eps = [_FakeEP(name="broken", raises=ImportError("nope"))]
    monkeypatch.setattr(ed, "entry_points", lambda group=None, **_: eps)
    assert load_drivers() == {}


def test_load_drivers_skips_non_protocol_target(monkeypatch):
    # Target loads cleanly but the instance has no Protocol methods.
    eps = [_FakeEP(name="bogus", target=lambda: object())]
    monkeypatch.setattr(ed, "entry_points", lambda group=None, **_: eps)
    assert "bogus" not in load_drivers()


def test_load_drivers_last_write_wins(monkeypatch):
    d1 = NoopDriver(name="dup")
    d2 = NoopDriver(name="dup")
    eps = [
        _FakeEP(name="dup", target=lambda: d1),
        _FakeEP(name="dup", target=lambda: d2),
    ]
    monkeypatch.setattr(ed, "entry_points", lambda group=None, **_: eps)
    loaded = load_drivers()
    assert loaded["dup"] is d2


def test_load_drivers_accepts_class_or_instance(monkeypatch):
    class _D(NoopDriver):
        pass

    instance = NoopDriver(name="inst")
    eps = [
        _FakeEP(name="cls", target=_D),
        _FakeEP(name="inst", target=instance),
    ]
    monkeypatch.setattr(ed, "entry_points", lambda group=None, **_: eps)
    loaded = load_drivers()
    assert isinstance(loaded["cls"], _D)
    assert loaded["inst"] is instance


def test_list_driver_eps_returns_iterable(monkeypatch):
    eps = [_FakeEP(name="a"), _FakeEP(name="b")]
    monkeypatch.setattr(ed, "entry_points", lambda group=None, **_: eps)
    out = list_driver_eps()
    assert [e.name for e in out] == ["a", "b"]
