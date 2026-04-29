"""Unit tests for `po doctor`."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any

from typer.testing import CliRunner

from prefect_orchestration import doctor as doctor_mod
from prefect_orchestration import deployments as deployments_mod
from prefect_orchestration.cli import app
from prefect_orchestration.doctor import (
    CheckResult,
    DoctorCheck,
    DoctorReport,
    Status,
    render_table,
    run_doctor,
)


@dataclass
class FakeEntryPoint:
    name: str
    target: Any = None
    raises: Exception | None = None

    def load(self) -> Any:
        if self.raises is not None:
            raise self.raises
        return self.target


@dataclass
class FakeProc:
    returncode: int
    stdout: str = ""
    stderr: str = ""


# -- bd check -----------------------------------------------------------


def test_bd_missing_from_path(monkeypatch):
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda _: None)
    r = doctor_mod.check_bd_on_path()
    assert r.status is Status.FAIL
    assert r.remediation


def test_bd_present_and_runnable(monkeypatch):
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda _: "/usr/bin/bd")
    monkeypatch.setattr(
        doctor_mod.subprocess,
        "run",
        lambda *a, **k: FakeProc(returncode=0, stdout="bd 0.23.1\n"),
    )
    r = doctor_mod.check_bd_on_path()
    assert r.status is Status.OK
    assert "bd 0.23.1" in r.message


def test_bd_version_timeout(monkeypatch):
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda _: "/usr/bin/bd")

    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd="bd", timeout=5)

    monkeypatch.setattr(doctor_mod.subprocess, "run", _raise)
    r = doctor_mod.check_bd_on_path()
    assert r.status is Status.FAIL


def test_bd_version_nonzero(monkeypatch):
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda _: "/usr/bin/bd")
    monkeypatch.setattr(
        doctor_mod.subprocess, "run", lambda *a, **k: FakeProc(returncode=2)
    )
    r = doctor_mod.check_bd_on_path()
    assert r.status is Status.FAIL


# -- prefect api --------------------------------------------------------


def test_prefect_api_unset(monkeypatch):
    monkeypatch.delenv("PREFECT_API_URL", raising=False)
    r = doctor_mod.check_prefect_api_reachable()
    assert r.status is Status.FAIL
    assert "PREFECT_API_URL" in r.message


# -- work pool ----------------------------------------------------------


def test_work_pool_skipped_when_api_unset(monkeypatch):
    monkeypatch.delenv("PREFECT_API_URL", raising=False)
    r = doctor_mod.check_work_pool_exists()
    assert r.status is Status.FAIL
    assert "skipped" in r.message.lower()


# -- deployment pools exist --------------------------------------------


@dataclass
class _FakeDep:
    name: str = "demo"
    work_pool_name: str | None = None


def _fake_loader(deps: list[_FakeDep]):
    def _load() -> tuple[list[Any], list[Any]]:
        return (
            [deployments_mod.LoadedDeployment(pack="p", deployment=d) for d in deps],
            [],
        )

    return _load


def test_deployment_pools_no_pinned_deployments(monkeypatch):
    monkeypatch.setattr(
        doctor_mod._deployments,
        "load_deployments",
        _fake_loader([_FakeDep(work_pool_name=None)]),
    )
    r = doctor_mod.check_deployment_pools_exist()
    assert r.status is Status.OK
    assert "no pool-bound deployments" in r.message


def test_deployment_pools_warns_on_missing_pool(monkeypatch):
    monkeypatch.setenv("PREFECT_API_URL", "http://test/api")
    monkeypatch.setattr(
        doctor_mod._deployments,
        "load_deployments",
        _fake_loader([_FakeDep(work_pool_name="ghost")]),
    )
    monkeypatch.setattr(doctor_mod, "_read_pool_names", lambda: ["po"])
    r = doctor_mod.check_deployment_pools_exist()
    assert r.status is Status.WARN
    assert "ghost" in r.message
    assert "work-pool create" in r.remediation


def test_deployment_pools_ok_when_pool_exists(monkeypatch):
    monkeypatch.setenv("PREFECT_API_URL", "http://test/api")
    monkeypatch.setattr(
        doctor_mod._deployments,
        "load_deployments",
        _fake_loader([_FakeDep(work_pool_name="po-k8s")]),
    )
    monkeypatch.setattr(doctor_mod, "_read_pool_names", lambda: ["po", "po-k8s"])
    r = doctor_mod.check_deployment_pools_exist()
    assert r.status is Status.OK
    assert "1 pinned deployment" in r.message


def test_deployment_pools_skipped_when_api_unset(monkeypatch):
    monkeypatch.delenv("PREFECT_API_URL", raising=False)
    monkeypatch.setattr(
        doctor_mod._deployments,
        "load_deployments",
        _fake_loader([_FakeDep(work_pool_name="po-k8s")]),
    )
    r = doctor_mod.check_deployment_pools_exist()
    assert r.status is Status.WARN
    assert "skipped" in r.message.lower()


# -- formulas / deployments --------------------------------------------


def test_formulas_load_empty(monkeypatch):
    monkeypatch.setattr(doctor_mod, "_iter_formula_eps", lambda: [])
    r = doctor_mod.check_formulas_load()
    assert r.status is Status.FAIL


def test_formulas_load_ok(monkeypatch):
    monkeypatch.setattr(
        doctor_mod,
        "_iter_formula_eps",
        lambda: [
            FakeEntryPoint(name="a", target=object()),
            FakeEntryPoint(name="b", target=object()),
        ],
    )
    r = doctor_mod.check_formulas_load()
    assert r.status is Status.OK
    assert "2" in r.message


def test_formulas_load_raises(monkeypatch):
    monkeypatch.setattr(
        doctor_mod,
        "_iter_formula_eps",
        lambda: [FakeEntryPoint(name="broken", raises=ImportError("nope"))],
    )
    r = doctor_mod.check_formulas_load()
    assert r.status is Status.FAIL
    assert "broken" in r.message


def test_deployments_load_ok(monkeypatch):
    monkeypatch.setattr(deployments_mod, "_iter_entry_points", lambda: [])
    r = doctor_mod.check_deployments_load()
    assert r.status is Status.OK


def test_deployments_load_errors(monkeypatch):
    def _fake_loader():
        return (
            [],
            [deployments_mod.LoadError(pack="p1", error="boom")],
        )

    monkeypatch.setattr(doctor_mod._deployments, "load_deployments", _fake_loader)
    r = doctor_mod.check_deployments_load()
    assert r.status is Status.FAIL
    assert "p1" in r.message


def test_po_list_nonempty_ok(monkeypatch):
    monkeypatch.setattr(
        doctor_mod, "_iter_formula_eps", lambda: [FakeEntryPoint(name="a")]
    )
    assert doctor_mod.check_po_list_nonempty().status is Status.OK


def test_po_list_nonempty_fails_when_empty(monkeypatch):
    monkeypatch.setattr(doctor_mod, "_iter_formula_eps", lambda: [])
    assert doctor_mod.check_po_list_nonempty().status is Status.FAIL


# -- uv-tool fresh ------------------------------------------------------


def test_uv_tool_fresh_po_not_on_path(monkeypatch):
    monkeypatch.setattr(doctor_mod, "_iter_formula_eps", lambda: [])
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda _: None)
    r = doctor_mod.check_uv_tool_fresh()
    assert r.status is Status.WARN


def test_uv_tool_fresh_matches(monkeypatch):
    monkeypatch.setattr(
        doctor_mod,
        "_iter_formula_eps",
        lambda: [FakeEntryPoint(name="foo"), FakeEntryPoint(name="bar")],
    )
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda _: "/usr/bin/po")
    monkeypatch.setattr(
        doctor_mod.subprocess,
        "run",
        lambda *a, **k: FakeProc(
            returncode=0,
            stdout="  foo  po_formulas.x:foo\n  bar  po_formulas.x:bar\n",
        ),
    )
    r = doctor_mod.check_uv_tool_fresh()
    assert r.status is Status.OK


def test_uv_tool_fresh_divergence(monkeypatch):
    monkeypatch.setattr(
        doctor_mod, "_iter_formula_eps", lambda: [FakeEntryPoint(name="foo")]
    )
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda _: "/usr/bin/po")
    monkeypatch.setattr(
        doctor_mod.subprocess,
        "run",
        lambda *a, **k: FakeProc(returncode=0, stdout="  extra  po_formulas.x:extra\n"),
    )
    r = doctor_mod.check_uv_tool_fresh()
    assert r.status is Status.WARN
    assert "uv tool install" in r.remediation


# -- logfire ------------------------------------------------------------


def test_logfire_set(monkeypatch):
    monkeypatch.setenv("LOGFIRE_TOKEN", "abc")
    assert doctor_mod.check_logfire_token().status is Status.OK


def test_logfire_unset(monkeypatch):
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    r = doctor_mod.check_logfire_token()
    assert r.status is Status.WARN
    assert r.remediation


# -- aggregator / exit code --------------------------------------------


def _ok(name: str) -> CheckResult:
    return CheckResult(name=name, status=Status.OK, message="")


def _warn(name: str) -> CheckResult:
    return CheckResult(name=name, status=Status.WARN, message="", remediation="r")


def _fail(name: str) -> CheckResult:
    return CheckResult(name=name, status=Status.FAIL, message="x", remediation="r")


def test_exit_code_zero_when_all_ok():
    report = run_doctor([lambda: _ok("a"), lambda: _warn("b")])
    assert report.exit_code == 0


def test_exit_code_one_when_any_fail():
    report = run_doctor([lambda: _ok("a"), lambda: _fail("b"), lambda: _warn("c")])
    assert report.exit_code == 1


def test_warnings_never_set_exit_code():
    report = run_doctor([lambda: _warn("a"), lambda: _warn("b")])
    assert report.exit_code == 0
    assert len(report.warnings) == 2


def test_per_check_exception_becomes_fail():
    def _boom() -> CheckResult:
        raise RuntimeError("splat")

    report = run_doctor([_boom])
    assert report.exit_code == 1
    assert report.failures[0].status is Status.FAIL
    assert "splat" in report.failures[0].message


def test_fail_rows_always_carry_remediation():
    """AC 3: red lines include a remediation hint."""
    # Force every check into FAIL by monkeypatching dependencies.
    report = run_doctor([lambda: _fail(f"c{i}") for i in range(5)])
    assert all(r.remediation for r in report.failures)


def test_render_table_contains_header_and_remediation():
    """AC 1: per-check table. AC 3: remediation under FAIL rows."""
    report = DoctorReport(results=[_ok("good"), _fail("bad")])
    out = render_table(report)
    assert "CHECK" in out and "STATUS" in out and "MESSAGE" in out
    assert "good" in out and "bad" in out
    assert "-> r" in out  # remediation line for the FAIL row
    assert "1 failure(s), 0 warning(s)." in out


def test_run_doctor_writes_nothing_to_disk(tmp_path, monkeypatch):
    """AC 4: idempotent, no state written."""
    monkeypatch.chdir(tmp_path)
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    run_doctor([lambda: _ok("a"), lambda: _warn("b"), lambda: _fail("c")])
    after = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    assert before == after


# -- CLI integration ---------------------------------------------------


def test_cli_doctor_runs_and_renders(monkeypatch):
    """`po doctor` prints the table and exits 0 when no FAILs."""
    monkeypatch.setattr(
        doctor_mod,
        "ALL_CHECKS",
        [lambda: _ok("a"), lambda: _warn("b")],
    )
    # Suppress pack-contributed checks so the test isn't sensitive to which
    # packs happen to be installed in the dev venv (e.g. po-stripe ships
    # checks that fail on a system without the stripe CLI).
    monkeypatch.setattr(doctor_mod, "_iter_doctor_check_eps", lambda: [])
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "CHECK" in result.stdout
    assert "a" in result.stdout and "b" in result.stdout


def test_cli_doctor_exits_one_on_failure(monkeypatch):
    monkeypatch.setattr(
        doctor_mod, "ALL_CHECKS", [lambda: _ok("a"), lambda: _fail("b")]
    )
    monkeypatch.setattr(doctor_mod, "_iter_doctor_check_eps", lambda: [])
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "-> r" in result.stdout


# -- pack-contributed doctor checks (po.doctor_checks group) -----------


@dataclass
class FakeDist:
    name: str


@dataclass
class FakeDoctorEP:
    name: str
    target: Any = None
    raises: Exception | None = None
    dist: FakeDist | None = None

    def load(self) -> Any:
        if self.raises is not None:
            raise self.raises
        return self.target


def test_doctor_check_dataclass_shape():
    """AC 3: name/status/message/hint."""
    dc = DoctorCheck(name="x", status="green", message="ok")
    assert dc.name == "x"
    assert dc.status == "green"
    assert dc.message == "ok"
    assert dc.hint == ""
    dc2 = DoctorCheck(name="y", status="red", message="bad", hint="fix it")
    assert dc2.hint == "fix it"


def test_iter_doctor_check_eps_sorted_by_dist(monkeypatch):
    """AC 1 + install-order: deterministic alphabetical-by-dist sort."""
    eps = [
        FakeDoctorEP(name="b-check", dist=FakeDist(name="zeta-pack")),
        FakeDoctorEP(name="a-check", dist=FakeDist(name="alpha-pack")),
    ]

    def _fake_eps(group: str | None = None, **_kwargs):
        assert group == "po.doctor_checks"
        return eps

    monkeypatch.setattr(doctor_mod, "entry_points", _fake_eps)
    out = doctor_mod._iter_doctor_check_eps()
    assert [ep.name for ep in out] == ["a-check", "b-check"]


def test_run_pack_check_green_to_ok(monkeypatch):
    """AC 2/3: green DoctorCheck round-trips to a CheckResult with source."""
    ep = FakeDoctorEP(
        name="my-check",
        target=lambda: DoctorCheck(name="my-check", status="green", message="all good"),
        dist=FakeDist(name="my-pack"),
    )
    res = doctor_mod._run_pack_check(ep)
    assert res.status is Status.OK
    assert res.source == "my-pack"
    assert res.message == "all good"


def test_run_pack_check_red_to_fail_with_hint(monkeypatch):
    ep = FakeDoctorEP(
        name="my-check",
        target=lambda: DoctorCheck(
            name="my-check", status="red", message="broken", hint="reinstall"
        ),
        dist=FakeDist(name="my-pack"),
    )
    res = doctor_mod._run_pack_check(ep)
    assert res.status is Status.FAIL
    assert res.remediation == "reinstall"


def test_run_pack_check_yellow_on_timeout(monkeypatch):
    """AC 4: per-check timeout, yellow on timeout."""
    import time

    def _slow() -> DoctorCheck:
        time.sleep(5)  # well past our 0.05s ceiling
        return DoctorCheck(name="slow", status="green", message="never reached")

    ep = FakeDoctorEP(name="slow", target=_slow, dist=FakeDist(name="my-pack"))
    res = doctor_mod._run_pack_check(ep, timeout=0.05)
    assert res.status is Status.WARN
    assert "timed out" in res.message.lower()
    assert res.source == "my-pack"


def test_run_pack_check_exception_to_fail():
    def _boom() -> DoctorCheck:
        raise RuntimeError("kaboom")

    ep = FakeDoctorEP(name="oops", target=_boom, dist=FakeDist(name="my-pack"))
    res = doctor_mod._run_pack_check(ep)
    assert res.status is Status.FAIL
    assert "kaboom" in res.message


def test_run_pack_check_invalid_status_to_fail():
    ep = FakeDoctorEP(
        name="weird",
        target=lambda: DoctorCheck(name="weird", status="blue", message="?"),  # type: ignore[arg-type]
        dist=FakeDist(name="my-pack"),
    )
    res = doctor_mod._run_pack_check(ep)
    assert res.status is Status.FAIL
    assert "invalid status" in res.message.lower()


def test_run_pack_check_non_doctorcheck_to_fail():
    ep = FakeDoctorEP(
        name="badret",
        target=lambda: "not a DoctorCheck",
        dist=FakeDist(name="my-pack"),
    )
    res = doctor_mod._run_pack_check(ep)
    assert res.status is Status.FAIL
    assert "expected DoctorCheck" in res.message


def test_run_doctor_aggregates_pack_checks(monkeypatch):
    """AC 2: core + pack rows in one report; pack rows tagged with source."""

    def _pack_ep_iter():
        return [
            FakeDoctorEP(
                name="pack-check",
                target=lambda: DoctorCheck(
                    name="pack-check", status="green", message="ok"
                ),
                dist=FakeDist(name="example-pack"),
            )
        ]

    monkeypatch.setattr(doctor_mod, "ALL_CHECKS", [lambda: _ok("core-1")])
    monkeypatch.setattr(doctor_mod, "_iter_doctor_check_eps", _pack_ep_iter)
    report = run_doctor()
    sources = [r.source for r in report.results]
    assert "core" in sources
    assert "example-pack" in sources
    pack_row = next(r for r in report.results if r.source == "example-pack")
    assert pack_row.status is Status.OK


def test_run_doctor_pack_red_sets_exit_one(monkeypatch):
    monkeypatch.setattr(doctor_mod, "ALL_CHECKS", [lambda: _ok("core-1")])
    monkeypatch.setattr(
        doctor_mod,
        "_iter_doctor_check_eps",
        lambda: [
            FakeDoctorEP(
                name="bad",
                target=lambda: DoctorCheck(
                    name="bad", status="red", message="x", hint="h"
                ),
                dist=FakeDist(name="p"),
            )
        ],
    )
    report = run_doctor()
    assert report.exit_code == 1


def test_run_doctor_explicit_checks_skips_pack(monkeypatch):
    """When tests pass an explicit `checks` arg, pack checks are skipped."""
    called = {"n": 0}

    def _should_not_be_called():
        called["n"] += 1
        return []

    monkeypatch.setattr(doctor_mod, "_iter_doctor_check_eps", _should_not_be_called)
    run_doctor([lambda: _ok("a")])
    assert called["n"] == 0


def test_render_table_includes_source_column():
    report = DoctorReport(
        results=[
            CheckResult(name="core-c", status=Status.OK, message="m1"),
            CheckResult(
                name="pack-c",
                status=Status.OK,
                message="m2",
                source="example-pack",
            ),
        ]
    )
    out = render_table(report)
    assert "SOURCE" in out
    assert "core" in out
    assert "example-pack" in out
