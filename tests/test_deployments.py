"""Unit tests for deployment discovery + CLI `po deploy`."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from prefect.schedules import Cron, Interval
from typer.testing import CliRunner

from prefect_orchestration import deployments as deployments_mod
from prefect_orchestration.cli import app
from tests._fixtures import sample_flow


@dataclass
class FakeEntryPoint:
    name: str
    target: Any

    def load(self) -> Any:
        return self.target


@pytest.fixture
def patch_eps(monkeypatch):
    """Replace `_iter_entry_points` with a controllable list."""

    def _set(eps: list[FakeEntryPoint]) -> None:
        monkeypatch.setattr(deployments_mod, "_iter_entry_points", lambda: list(eps))

    return _set


def _nightly() -> Any:
    return sample_flow.to_deployment(
        name="nightly",
        schedule=Cron("0 9 * * *", timezone="America/New_York"),
        parameters={"x": 42},
    )


# -- discovery ----------------------------------------------------------


def test_load_deployments_empty(patch_eps):
    patch_eps([])
    loaded, errors = deployments_mod.load_deployments()
    assert loaded == []
    assert errors == []


def test_load_deployments_single_and_list(patch_eps):
    def reg_single():
        return _nightly()

    def reg_list():
        return [_nightly(), sample_flow.to_deployment(name="manual")]

    patch_eps([FakeEntryPoint("a", reg_single), FakeEntryPoint("b", reg_list)])
    loaded, errors = deployments_mod.load_deployments()
    assert errors == []
    packs = [d.pack for d in loaded]
    assert packs == ["a", "b", "b"]


def test_load_deployments_register_raises_is_collected(patch_eps):
    def reg_bad():
        raise RuntimeError("boom")

    def reg_good():
        return _nightly()

    patch_eps([FakeEntryPoint("bad", reg_bad), FakeEntryPoint("good", reg_good)])
    loaded, errors = deployments_mod.load_deployments()
    assert len(loaded) == 1 and loaded[0].pack == "good"
    assert len(errors) == 1 and errors[0].pack == "bad" and "boom" in errors[0].error


def test_load_deployments_non_callable(patch_eps):
    patch_eps([FakeEntryPoint("weird", 123)])
    loaded, errors = deployments_mod.load_deployments()
    assert loaded == []
    assert len(errors) == 1 and "not callable" in errors[0].error


def test_load_formula_flows_skip_and_collect_errors(monkeypatch):
    def good():
        return "ok"

    class Boom:
        def load(self):
            raise RuntimeError("bad formula")

    monkeypatch.setattr(
        deployments_mod,
        "iter_formula_entry_points",
        lambda: [
            FakeEntryPoint("skip-me", good),
            FakeEntryPoint("good", good),
            BoomEP("bad"),
        ],
    )

    flows, errors = deployments_mod.load_formula_flows(skip_names={"skip-me"})
    assert flows == {"good": good}
    assert len(errors) == 1
    assert errors[0].pack == "bad"
    assert "bad formula" in errors[0].error


@dataclass
class BoomEP:
    name: str

    def load(self) -> Any:
        raise RuntimeError("bad formula")


def test_build_cron_deployments_from_order_dir(tmp_path: Path, monkeypatch):
    orders = tmp_path / "orders"
    orders.mkdir()
    (orders / "nightly.toml").write_text(
        'cron = "0 9 * * *"\n'
        'formula = "hello"\n'
        'timezone = "America/New_York"\n'
        "[params]\n"
        "x = 42\n"
    )
    monkeypatch.setattr(
        deployments_mod,
        "load_formula_flows",
        lambda **kwargs: ({"hello": sample_flow}, []),
    )

    built = deployments_mod.build_cron_deployments_from_order_dir(
        orders,
        tag_prefix="demo-pack",
    )
    assert len(built) == 1
    dep = built[0]
    assert dep.name == "nightly"
    assert dep.parameters == {"x": 42}
    assert list(dep.tags) == ["demo-pack", "hello"]
    schedule = dep.schedules[0].schedule
    assert schedule.cron == "0 9 * * *"
    assert schedule.timezone == "America/New_York"


def test_build_cron_deployments_skips_unknown_formula_with_warning(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
):
    orders = tmp_path / "orders"
    orders.mkdir()
    (orders / "nightly.toml").write_text('cron = "0 9 * * *"\nformula = "missing"\n')
    monkeypatch.setattr(
        deployments_mod, "load_formula_flows", lambda **kwargs: ({}, [])
    )
    caplog.set_level(logging.WARNING, logger=deployments_mod.logger.name)

    built = deployments_mod.build_cron_deployments_from_order_dir(
        orders,
        tag_prefix="demo-pack",
    )
    assert built == []
    assert any("missing" in rec.message for rec in caplog.records)


# -- formatting ---------------------------------------------------------


def test_format_schedule_cron():
    dep = _nightly()
    out = deployments_mod.format_schedule(dep)
    assert "cron(0 9 * * *" in out
    assert "America/New_York" in out


def test_format_schedule_interval():
    from datetime import timedelta

    dep = sample_flow.to_deployment(name="i", schedule=Interval(timedelta(minutes=5)))
    assert deployments_mod.format_schedule(dep).startswith("interval(")


def test_format_schedule_manual():
    dep = sample_flow.to_deployment(name="m")
    assert deployments_mod.format_schedule(dep) == "manual"


# -- apply --------------------------------------------------------------


class _Spy:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.work_pool_name: str | None = None
        self.name = "spy"

    def apply(self, *args, **kwargs):
        self.calls.append({"wp": self.work_pool_name})
        return "uuid-1234"


def test_apply_deployment_sets_work_pool_and_returns_id():
    spy = _Spy()
    dep_id = deployments_mod.apply_deployment(spy, work_pool="po")
    assert dep_id == "uuid-1234"
    assert spy.work_pool_name == "po"
    assert len(spy.calls) == 1


# -- CLI ----------------------------------------------------------------


def test_cli_deploy_empty(patch_eps):
    patch_eps([])
    res = CliRunner().invoke(app, ["deploy"])
    assert res.exit_code == 0
    assert "no deployments registered" in res.stdout


def test_cli_deploy_lists(patch_eps):
    patch_eps([FakeEntryPoint("mypack", lambda: _nightly())])
    res = CliRunner().invoke(app, ["deploy"])
    assert res.exit_code == 0, res.stdout
    assert "mypack" in res.stdout
    assert "nightly" in res.stdout
    assert "cron(0 9 * * *" in res.stdout


def test_cli_deploy_filter_by_pack(patch_eps):
    patch_eps(
        [
            FakeEntryPoint("a", lambda: sample_flow.to_deployment(name="one")),
            FakeEntryPoint("b", lambda: sample_flow.to_deployment(name="two")),
        ]
    )
    res = CliRunner().invoke(app, ["deploy", "--pack", "a"])
    assert res.exit_code == 0
    assert "one" in res.stdout
    assert "two" not in res.stdout


def test_cli_deploy_apply_without_api_url_exits_2(patch_eps, monkeypatch):
    monkeypatch.delenv("PREFECT_API_URL", raising=False)
    patch_eps([FakeEntryPoint("mypack", lambda: _nightly())])
    res = CliRunner().invoke(app, ["deploy", "--apply"])
    assert res.exit_code == 2
    assert "PREFECT_API_URL" in res.stderr or "PREFECT_API_URL" in res.output


def test_cli_deploy_apply_calls_apply(patch_eps, monkeypatch):
    monkeypatch.setenv("PREFECT_API_URL", "http://127.0.0.1:4200/api")
    spies = [_Spy(), _Spy()]
    spies[0].name = "one"
    spies[1].name = "two"
    patch_eps(
        [
            FakeEntryPoint("a", lambda: spies[0]),
            FakeEntryPoint("b", lambda: spies[1]),
        ]
    )
    res = CliRunner().invoke(app, ["deploy", "--apply", "--work-pool", "po"])
    assert res.exit_code == 0, res.stdout
    assert all(len(s.calls) == 1 for s in spies)
    assert all(s.work_pool_name == "po" for s in spies)
    assert "OK" in res.stdout


def test_cli_deploy_apply_continues_past_failure(patch_eps, monkeypatch):
    monkeypatch.setenv("PREFECT_API_URL", "http://127.0.0.1:4200/api")

    class Boom:
        name = "boom"
        work_pool_name = None

        def apply(self):
            raise RuntimeError("server down")

    ok = _Spy()
    ok.name = "ok"
    patch_eps(
        [
            FakeEntryPoint("bad", lambda: Boom()),
            FakeEntryPoint("good", lambda: ok),
        ]
    )
    res = CliRunner().invoke(app, ["deploy", "--apply"])
    assert res.exit_code == 1
    assert len(ok.calls) == 1


# -- backward-compat smoke for `po run` / `po list` ---------------------


def test_po_list_still_works(monkeypatch):
    # No formulas AND no commands installed → friendly message, exit 0.
    # `po list` lists both formula and command entry points; stub both.
    from prefect_orchestration import cli as cli_mod
    from prefect_orchestration import commands as commands_mod

    monkeypatch.setattr(cli_mod, "_load_formulas", lambda: {})
    monkeypatch.setattr(commands_mod, "load_commands", lambda: {})
    res = CliRunner().invoke(app, ["list"])
    assert res.exit_code == 0
    assert "no formulas" in res.stdout


# -- real-EP integration: the po-formulas pack example ----------------


def test_po_formulas_pack_exposes_epic_sr_8yu_nightly():
    """Run the installed `po` console script and assert the po-formulas pack's
    `epic-sr-8yu-nightly` Cron deployment shows up in the listing (AC2).

    Runs in a subprocess so pytest's rootdir-on-sys.path doesn't shadow the
    editable po-formulas install. Skips when the `po` script or the pack is
    not present.
    """
    import subprocess
    import sys
    from pathlib import Path

    # Prefer the `po` shipped with the current Python's venv (pytest's
    # interpreter) so this test validates the in-tree wiring.
    po = Path(sys.executable).with_name("po")
    if not po.exists():
        pytest.skip("`po` console script not installed in this venv")
    # Run from /tmp so pytest rootdir isn't inherited as cwd.
    result = subprocess.run(
        [str(po), "deploy"], capture_output=True, text=True, cwd="/tmp", timeout=30
    )
    out = result.stdout + result.stderr
    if "no deployments registered" in out:
        pytest.skip("po-formulas-software-dev pack not installed in this venv")
    assert result.returncode == 0, out
    assert "software-dev" in out
    assert "epic-sr-8yu-nightly" in out
    assert "cron(0 9 * * *" in out
    assert "America/New_York" in out
    assert "epic_id" in out


def test_po_run_still_works(monkeypatch):
    from prefect_orchestration import cli as cli_mod

    captured = {}

    def fake_flow(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(cli_mod, "_load_formulas", lambda: {"hello": fake_flow})
    res = CliRunner().invoke(app, ["run", "hello", "--who", "world", "--n", "3"])
    assert res.exit_code == 0, res.stdout
    assert captured == {"who": "world", "n": 3}


# -- apply_rig_deployments -----------------------------------------------


class _SpyDep:
    """Fake RunnerDeployment that records what apply_rig_deployments writes into it."""

    def __init__(self, name: str, work_pool_name: str | None = None) -> None:
        self.name = name
        self.work_pool_name = work_pool_name
        self.parameters: dict = {}
        self.apply_calls: list[dict] = []

    def apply(self, *args, **kwargs) -> str:
        self.apply_calls.append(
            {
                "name": self.name,
                "wp": self.work_pool_name,
                "params": dict(self.parameters),
            }
        )
        return f"id-{self.name}"


def _patch_load_deployments(monkeypatch, deps: list[tuple[str, _SpyDep]]) -> None:
    loaded = [
        deployments_mod.LoadedDeployment(pack=pack, deployment=dep)
        for pack, dep in deps
    ]
    monkeypatch.setattr(deployments_mod, "load_deployments", lambda: (loaded, []))


def test_apply_rig_deployments_scopes_name_and_injects_params(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dep = _SpyDep("nightly", work_pool_name="po")
    _patch_load_deployments(monkeypatch, [("mypack", dep)])
    monkeypatch.setattr(
        deployments_mod, "ensure_work_pool", lambda *a, **k: ("po", False)
    )

    rig = tmp_path / "my-rig"
    rig.mkdir()
    applied, errors = deployments_mod.apply_rig_deployments(rig)

    assert errors == []
    assert len(applied) == 1
    r = applied[0]
    assert r.pack == "mypack"
    assert r.deployment_name == "nightly-my-rig"
    assert r.ok
    # Deployment was mutated with rig-scoped name + params
    assert dep.name == "nightly-my-rig"
    assert dep.parameters["rig_path"] == str(rig)
    assert dep.parameters["rig"] == "my-rig"


def test_apply_rig_deployments_work_pool_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dep = _SpyDep("nightly", work_pool_name="original-pool")
    _patch_load_deployments(monkeypatch, [("mypack", dep)])
    monkeypatch.setattr(
        deployments_mod, "ensure_work_pool", lambda *a, **k: (a[0], False)
    )

    rig = tmp_path / "biz"
    rig.mkdir()
    applied, errors = deployments_mod.apply_rig_deployments(
        rig, work_pool="override-pool"
    )

    assert not errors
    assert dep.work_pool_name == "override-pool"


def test_apply_rig_deployments_does_not_overwrite_explicit_rig_params(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dep = _SpyDep("nightly")
    dep.parameters = {"rig_path": "/pack-side-value", "rig": "pack-rig"}
    _patch_load_deployments(monkeypatch, [("mypack", dep)])
    monkeypatch.setattr(
        deployments_mod, "ensure_work_pool", lambda *a, **k: (a[0], False)
    )

    rig = tmp_path / "rig"
    rig.mkdir()
    deployments_mod.apply_rig_deployments(rig)

    # setdefault semantics — pack-provided values win
    assert dep.parameters["rig_path"] == "/pack-side-value"
    assert dep.parameters["rig"] == "pack-rig"


def test_apply_rig_deployments_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(deployments_mod, "load_deployments", lambda: ([], []))
    rig = tmp_path / "rig"
    rig.mkdir()
    applied, errors = deployments_mod.apply_rig_deployments(rig)
    assert applied == []
    assert errors == []


def test_apply_rig_deployments_apply_error_collected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class BoomDep(_SpyDep):
        def apply(self, *args, **kwargs) -> str:
            raise RuntimeError("server down")

    dep = BoomDep("nightly")
    _patch_load_deployments(monkeypatch, [("mypack", dep)])
    monkeypatch.setattr(
        deployments_mod, "ensure_work_pool", lambda *a, **k: (a[0], False)
    )

    rig = tmp_path / "rig"
    rig.mkdir()
    applied, errors = deployments_mod.apply_rig_deployments(rig)
    assert applied == []
    assert len(errors) == 1
    assert "server down" in errors[0].error


def test_apply_rig_deployments_create_pools_called(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dep = _SpyDep("nightly", work_pool_name="po")
    _patch_load_deployments(monkeypatch, [("mypack", dep)])

    pools_ensured: list[str] = []
    monkeypatch.setattr(
        deployments_mod,
        "ensure_work_pool",
        lambda pn, **k: pools_ensured.append(pn) or (pn, True),
    )

    rig = tmp_path / "rig"
    rig.mkdir()
    deployments_mod.apply_rig_deployments(rig)
    assert "po" in pools_ensured


def test_apply_rig_deployments_skip_pools_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dep = _SpyDep("nightly", work_pool_name="po")
    _patch_load_deployments(monkeypatch, [("mypack", dep)])

    def boom(*a, **k):
        raise AssertionError("ensure_work_pool must not be called")

    monkeypatch.setattr(deployments_mod, "ensure_work_pool", boom)

    rig = tmp_path / "rig"
    rig.mkdir()
    deployments_mod.apply_rig_deployments(rig, create_pools=False)


# -- ensure_work_pool -----------------------------------------------------


def test_ensure_work_pool_creates_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pool doesn't exist → _ensure_pool_async returns True → ensure_work_pool returns created=True."""

    async def fake_ensure(pool_name: str, pool_type: str) -> bool:
        assert pool_name == "new-pool"
        return True

    monkeypatch.setattr(deployments_mod, "_ensure_pool_async", fake_ensure)

    _, was_created = deployments_mod.ensure_work_pool("new-pool")
    assert was_created is True


def test_ensure_work_pool_noop_when_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pool already exists → _ensure_pool_async returns False → ensure_work_pool returns created=False."""

    async def fake_ensure(pool_name: str, pool_type: str) -> bool:
        return False

    monkeypatch.setattr(deployments_mod, "_ensure_pool_async", fake_ensure)

    _, was_created = deployments_mod.ensure_work_pool("existing-pool")
    assert was_created is False
