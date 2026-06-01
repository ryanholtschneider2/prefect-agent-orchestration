"""Unit tests for the `po cron` sub-app (apply / list)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from prefect_orchestration import cli as cli_mod
from prefect_orchestration import deployments as deployments_mod
from prefect_orchestration.cli import app


def _write_order(orders: Path, name: str, formula: str = "hello") -> None:
    (orders / f"{name}.toml").write_text(
        'cron = "0 9 * * *"\n'
        f'formula = "{formula}"\n'
        'timezone = "America/New_York"\n'
        "[params]\n"
        "x = 42\n"
    )


class CronSchedule:
    """Minimal stand-in matching what `format_schedule` reads off a CronSchedule."""

    def __init__(self, cron: str, timezone: str) -> None:
        self.cron = cron
        self.timezone = timezone


class _ScheduleEntry:
    def __init__(self, schedule: CronSchedule) -> None:
        self.schedule = schedule


class _FakeDeployment:
    def __init__(self, name: str) -> None:
        self.name = name
        self.flow_name = "hello"
        self.work_pool_name: str | None = None
        self.applied_with: str | None = None
        self.schedules = [_ScheduleEntry(CronSchedule("0 9 * * *", "America/New_York"))]

    def apply(self, *args: Any, **kwargs: Any) -> str:
        self.applied_with = self.work_pool_name
        return f"uuid-{self.name}"


def _patch_builder(monkeypatch, deployments: list[_FakeDeployment]) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def _fake_build(orders_dir: Path, **kwargs: Any) -> list[Any]:
        seen["orders_dir"] = orders_dir
        seen["kwargs"] = kwargs
        return list(deployments)

    monkeypatch.setattr(
        deployments_mod, "build_cron_deployments_from_order_dir", _fake_build
    )
    monkeypatch.setattr(
        cli_mod._deployments, "build_cron_deployments_from_order_dir", _fake_build
    )
    return seen


def test_cron_apply_builds_and_applies(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PREFECT_API_URL", "http://127.0.0.1:4200/api")
    orders = tmp_path / "orders"
    orders.mkdir()
    _write_order(orders, "nightly")

    deps = [_FakeDeployment("nightly"), _FakeDeployment("weekly")]
    seen = _patch_builder(monkeypatch, deps)

    res = CliRunner().invoke(
        app, ["cron", "apply", "--orders-dir", str(orders), "--work-pool", "po"]
    )
    assert res.exit_code == 0, res.stdout
    # builder was called with the orders dir + forwarded knobs
    assert seen["orders_dir"] == orders
    assert seen["kwargs"]["work_pool_name"] == "po"
    # both deployments applied with the work pool propagated
    assert all(d.applied_with == "po" for d in deps)
    assert "nightly" in res.stdout
    assert "weekly" in res.stdout
    assert "OK" in res.stdout


def test_cron_apply_without_api_url_exits_2(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PREFECT_API_URL", raising=False)
    orders = tmp_path / "orders"
    orders.mkdir()
    _write_order(orders, "nightly")

    deps = [_FakeDeployment("nightly")]
    _patch_builder(monkeypatch, deps)

    res = CliRunner().invoke(app, ["cron", "apply", "--orders-dir", str(orders)])
    assert res.exit_code == 2
    assert "PREFECT_API_URL" in res.output
    # never applied
    assert deps[0].applied_with is None


def test_cron_apply_missing_dir_exits_2(tmp_path: Path):
    missing = tmp_path / "nope"
    res = CliRunner().invoke(app, ["cron", "apply", "--orders-dir", str(missing)])
    assert res.exit_code == 2
    assert "orders dir not found" in res.output


def test_cron_apply_no_deployments_exits_1(tmp_path: Path, monkeypatch):
    orders = tmp_path / "orders"
    orders.mkdir()
    _patch_builder(monkeypatch, [])

    res = CliRunner().invoke(app, ["cron", "apply", "--orders-dir", str(orders)])
    assert res.exit_code == 1
    assert "no cron deployments" in res.output


def test_cron_list_renders_without_applying(tmp_path: Path, monkeypatch):
    orders = tmp_path / "orders"
    orders.mkdir()
    _write_order(orders, "nightly")

    deps = [_FakeDeployment("nightly")]
    _patch_builder(monkeypatch, deps)
    # No server reachable -> SERVER column shows `?`, never applies.
    monkeypatch.setattr(cli_mod, "_cron_live_deployment_names", lambda: None)

    res = CliRunner().invoke(app, ["cron", "list", "--orders-dir", str(orders)])
    assert res.exit_code == 0, res.stdout
    assert "nightly" in res.stdout
    assert "cron(0 9 * * *" in res.stdout
    # apply path untouched
    assert deps[0].applied_with is None


def test_cron_list_cross_checks_server_state(tmp_path: Path, monkeypatch):
    orders = tmp_path / "orders"
    orders.mkdir()
    _write_order(orders, "nightly")

    deps = [_FakeDeployment("nightly"), _FakeDeployment("weekly")]
    _patch_builder(monkeypatch, deps)
    monkeypatch.setattr(
        cli_mod, "_cron_live_deployment_names", lambda: {"hello/nightly"}
    )

    res = CliRunner().invoke(app, ["cron", "list", "--orders-dir", str(orders)])
    assert res.exit_code == 0, res.stdout
    assert "live" in res.stdout
    assert "declared" in res.stdout


def test_cron_list_no_deployments_exits_1(tmp_path: Path, monkeypatch):
    orders = tmp_path / "orders"
    orders.mkdir()
    _patch_builder(monkeypatch, [])

    res = CliRunner().invoke(app, ["cron", "list", "--orders-dir", str(orders)])
    assert res.exit_code == 1
    assert "no cron deployments" in res.output
