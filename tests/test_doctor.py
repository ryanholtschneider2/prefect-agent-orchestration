"""Unit tests for `po doctor`."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any

import pytest
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


@pytest.fixture
def hide_pack_doctor_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide pack-contributed `po.doctor_checks` so CLI tests aren't sensitive
    to which packs happen to be installed in the dev venv (e.g. po-stripe
    ships checks that report red when the stripe CLI isn't installed).
    Opt-in — request the fixture in tests that exercise `po doctor` end-to-end.
    """
    monkeypatch.setattr(doctor_mod, "_iter_doctor_check_eps", lambda: [])


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


# -- check_pack_overlays -----------------------------------------------


def _make_fake_pack(tmp_path, name: str, *, with_overlay: bool = True):
    from prefect_orchestration.pack_overlay import Pack

    root = tmp_path / name
    root.mkdir(parents=True)
    module_root = root / name.replace("-", "_")
    module_root.mkdir()
    (module_root / "__init__.py").write_text("")
    if with_overlay:
        overlay_dir = root / "overlay"
        overlay_dir.mkdir()
        (overlay_dir / f"CLAUDE-{name}.md").write_text(f"# {name}")
    return Pack(name=name, root=root, module_root=module_root)


def test_check_pack_overlays_no_packs(monkeypatch, tmp_path):
    import prefect_orchestration.doctor as dm

    monkeypatch.setattr(dm, "check_pack_overlays", doctor_mod.check_pack_overlays)
    import prefect_orchestration.pack_overlay as po_mod

    monkeypatch.setattr(po_mod, "discover_packs", lambda: [])
    r = doctor_mod.check_pack_overlays()
    assert r.status is Status.OK
    assert "no packs" in r.message


def test_check_pack_overlays_all_present(monkeypatch, tmp_path):
    import prefect_orchestration.pack_overlay as po_mod

    packs = [_make_fake_pack(tmp_path, "po-mypack", with_overlay=True)]
    monkeypatch.setattr(po_mod, "discover_packs", lambda: packs)
    r = doctor_mod.check_pack_overlays()
    assert r.status is Status.OK
    assert "1 pack(s)" in r.message


def test_check_pack_overlays_missing(monkeypatch, tmp_path):
    import prefect_orchestration.pack_overlay as po_mod

    packs = [_make_fake_pack(tmp_path, "po-mypack", with_overlay=False)]
    monkeypatch.setattr(po_mod, "discover_packs", lambda: packs)
    r = doctor_mod.check_pack_overlays()
    assert r.status is Status.WARN
    assert "po-mypack" in r.message
    assert r.remediation


def test_check_pack_overlays_core_excluded(monkeypatch, tmp_path):
    """prefect-orchestration itself is not checked for an overlay."""
    import prefect_orchestration.pack_overlay as po_mod
    from prefect_orchestration.pack_overlay import Pack

    core = Pack(name="prefect-orchestration", root=tmp_path, module_root=tmp_path)
    monkeypatch.setattr(po_mod, "discover_packs", lambda: [core])
    r = doctor_mod.check_pack_overlays()
    assert r.status is Status.OK
    assert "no packs" in r.message


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


def test_cli_doctor_runs_and_renders(monkeypatch, hide_pack_doctor_checks):
    """`po doctor` prints the table and exits 0 when no FAILs."""
    monkeypatch.setattr(
        doctor_mod,
        "ALL_CHECKS",
        [lambda: _ok("a"), lambda: _warn("b")],
    )
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "CHECK" in result.stdout
    assert "a" in result.stdout and "b" in result.stdout


def test_cli_doctor_exits_one_on_failure(monkeypatch, hide_pack_doctor_checks):
    monkeypatch.setattr(
        doctor_mod, "ALL_CHECKS", [lambda: _ok("a"), lambda: _fail("b")]
    )
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


# ─── check_env_drivers_registered ─────────────────────────────────────


def test_check_env_drivers_registered_none(monkeypatch):
    from prefect_orchestration import env_drivers as ed

    monkeypatch.setattr(ed, "list_driver_eps", lambda: [])
    r = doctor_mod.check_env_drivers_registered()
    assert r.status is Status.OK
    assert "none registered" in r.message


def test_check_env_drivers_registered_lists_eps(monkeypatch):
    from prefect_orchestration import env_drivers as ed
    from prefect_orchestration.env_drivers import NoopDriver

    @dataclass
    class _Dist:
        name: str

    @dataclass
    class _EP:
        name: str
        dist: _Dist

    fake_ep = _EP(name="daytona", dist=_Dist(name="po-cloud-daytona"))
    monkeypatch.setattr(ed, "list_driver_eps", lambda: [fake_ep])
    monkeypatch.setattr(ed, "load_drivers", lambda: {"daytona": NoopDriver()})

    r = doctor_mod.check_env_drivers_registered()
    assert r.status is Status.OK
    # AC: driver name appears as a substring (no assertion on dist literal).
    assert "daytona" in r.message


def test_check_env_drivers_registered_warns_on_broken(monkeypatch):
    from prefect_orchestration import env_drivers as ed

    @dataclass
    class _Dist:
        name: str

    @dataclass
    class _EP:
        name: str
        dist: _Dist

    fake_ep = _EP(name="ghost", dist=_Dist(name="po-cloud-ghost"))
    monkeypatch.setattr(ed, "list_driver_eps", lambda: [fake_ep])
    monkeypatch.setattr(ed, "load_drivers", lambda: {})

    r = doctor_mod.check_env_drivers_registered()
    assert r.status is Status.WARN
    assert "ghost" in r.message
    assert r.remediation


# ─── check_stale_locks / clean_stale_locks ────────────────────────────


def test_check_stale_locks_no_planning_dir(tmp_path):
    result = doctor_mod.check_stale_locks(rig_path=tmp_path)
    assert result.status is Status.OK
    assert "no .planning/" in result.message


def test_check_stale_locks_no_stale_locks(tmp_path):
    lock = tmp_path / ".planning" / "software-dev-full" / "iss-1.retry.lock"
    lock.parent.mkdir(parents=True)
    lock.touch()
    result = doctor_mod.check_stale_locks(rig_path=tmp_path)
    assert result.status is Status.OK


def test_check_stale_locks_reports_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("PO_RETRY_LOCK_STALE_SECS", "60")
    lock = tmp_path / ".planning" / "software-dev-full" / "iss-2.retry.lock"
    lock.parent.mkdir(parents=True)
    lock.touch()
    old_time = time.time() - 300  # 5 min > 60s threshold
    os.utime(lock, (old_time, old_time))
    result = doctor_mod.check_stale_locks(rig_path=tmp_path)
    assert result.status is Status.WARN
    assert "iss-2" in result.message


def test_clean_stale_locks_removes_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("PO_RETRY_LOCK_STALE_SECS", "60")
    lock = tmp_path / ".planning" / "software-dev-full" / "iss-3.retry.lock"
    lock.parent.mkdir(parents=True)
    lock.touch()
    old_time = time.time() - 300
    os.utime(lock, (old_time, old_time))
    removed = doctor_mod.clean_stale_locks(rig_path=tmp_path)
    assert len(removed) == 1
    assert not lock.exists()


# ─── run_env_checks ───────────────────────────────────────────────────────────


@dataclass
class _FakeEnvRecord:
    name: str
    driver: str
    snapshot_tag: str
    pool: str
    opaque: Any
    rig_remote: str
    identity_hash: str
    created_at: str = ""
    last_run_at: str = ""


def test_run_env_checks_no_envs(monkeypatch):
    monkeypatch.setattr("prefect_orchestration.env.list_envs", lambda: [])
    results = doctor_mod.run_env_checks()
    assert len(results) == 1
    assert results[0].status is Status.OK
    assert "no envs registered" in results[0].message


def test_run_env_checks_snapshot_skipped_empty_tag(monkeypatch):
    rec = _FakeEnvRecord(
        name="myenv",
        driver="noop",
        snapshot_tag="",
        pool="po-env-myenv",
        opaque={},
        rig_remote="",
        identity_hash="abc123",
    )
    from prefect_orchestration.env_drivers import NoopDriver

    noop = NoopDriver()
    monkeypatch.setattr("prefect_orchestration.env.list_envs", lambda: [rec])
    monkeypatch.setattr(
        "prefect_orchestration.env_drivers.load_drivers", lambda: {"noop": noop}
    )
    monkeypatch.setattr(
        "prefect_orchestration.env.compute_identity_hash", lambda **kw: "abc123"
    )
    monkeypatch.setattr(
        "prefect_orchestration.doctor._check_env_pool_worker",
        lambda pool, prefix: CheckResult(
            name=f"{prefix}: pool-worker",
            status=Status.WARN,
            message="PREFECT_API_URL not set; skipping",
        ),
    )

    results = doctor_mod.run_env_checks()
    snapshot_rows = [r for r in results if r.name == "myenv: snapshot"]
    assert len(snapshot_rows) == 1
    assert snapshot_rows[0].status is Status.OK
    assert "skipping" in snapshot_rows[0].message


def test_run_env_checks_snapshot_mismatch(monkeypatch):
    rec = _FakeEnvRecord(
        name="myenv",
        driver="noop",
        snapshot_tag="stored123",
        pool="po-env-myenv",
        opaque={},
        rig_remote="",
        identity_hash="abc123",
    )
    from prefect_orchestration.env_drivers import NoopDriver

    noop = NoopDriver()
    monkeypatch.setattr("prefect_orchestration.env.list_envs", lambda: [rec])
    monkeypatch.setattr(
        "prefect_orchestration.env_drivers.load_drivers", lambda: {"noop": noop}
    )
    monkeypatch.setattr(
        "prefect_orchestration.env.compute_identity_hash", lambda **kw: "abc123"
    )
    monkeypatch.setattr(
        "prefect_orchestration.doctor._compute_local_pack_hash", lambda: "local999"
    )
    monkeypatch.setattr(
        "prefect_orchestration.doctor._check_env_pool_worker",
        lambda pool, prefix: CheckResult(
            name=f"{prefix}: pool-worker", status=Status.OK, message="ok"
        ),
    )

    results = doctor_mod.run_env_checks()
    snapshot_rows = [r for r in results if r.name == "myenv: snapshot"]
    assert snapshot_rows[0].status is Status.FAIL
    assert "local999" in snapshot_rows[0].message
    assert "stored123" in snapshot_rows[0].message


def test_run_env_checks_identity_mismatch(monkeypatch):
    rec = _FakeEnvRecord(
        name="myenv",
        driver="noop",
        snapshot_tag="",
        pool="po-env-myenv",
        opaque={},
        rig_remote="",
        identity_hash="oldhash",
    )
    from prefect_orchestration.env_drivers import NoopDriver

    noop = NoopDriver()
    monkeypatch.setattr("prefect_orchestration.env.list_envs", lambda: [rec])
    monkeypatch.setattr(
        "prefect_orchestration.env_drivers.load_drivers", lambda: {"noop": noop}
    )
    monkeypatch.setattr(
        "prefect_orchestration.env.compute_identity_hash", lambda **kw: "newhash"
    )
    monkeypatch.setattr(
        "prefect_orchestration.doctor._check_env_pool_worker",
        lambda pool, prefix: CheckResult(
            name=f"{prefix}: pool-worker", status=Status.OK, message="ok"
        ),
    )

    results = doctor_mod.run_env_checks()
    identity_rows = [r for r in results if r.name == "myenv: identity"]
    assert identity_rows[0].status is Status.WARN
    assert "drift" in identity_rows[0].message
    assert identity_rows[0].remediation


def test_run_env_checks_driver_not_registered(monkeypatch):
    rec = _FakeEnvRecord(
        name="myenv",
        driver="ghost",
        snapshot_tag="",
        pool="po-env-myenv",
        opaque={},
        rig_remote="",
        identity_hash="x",
    )
    monkeypatch.setattr("prefect_orchestration.env.list_envs", lambda: [rec])
    monkeypatch.setattr("prefect_orchestration.env_drivers.load_drivers", lambda: {})
    monkeypatch.setattr(
        "prefect_orchestration.env.compute_identity_hash", lambda **kw: "x"
    )
    monkeypatch.setattr(
        "prefect_orchestration.doctor._check_env_pool_worker",
        lambda pool, prefix: CheckResult(
            name=f"{prefix}: pool-worker", status=Status.OK, message="ok"
        ),
    )

    results = doctor_mod.run_env_checks()
    driver_rows = [r for r in results if r.name == "myenv: driver"]
    assert driver_rows[0].status is Status.WARN
    assert "ghost" in driver_rows[0].message


def test_run_env_checks_driver_health_fail(monkeypatch):
    rec = _FakeEnvRecord(
        name="myenv",
        driver="noop",
        snapshot_tag="",
        pool="po-env-myenv",
        opaque={},
        rig_remote="",
        identity_hash="x",
    )
    from prefect_orchestration.env_drivers import NoopDriver, EnvHealth

    noop = NoopDriver()

    def _bad_health(handle):
        return EnvHealth(ok=False, summary="unreachable")

    noop.health = _bad_health  # type: ignore[method-assign]
    monkeypatch.setattr("prefect_orchestration.env.list_envs", lambda: [rec])
    monkeypatch.setattr(
        "prefect_orchestration.env_drivers.load_drivers", lambda: {"noop": noop}
    )
    monkeypatch.setattr(
        "prefect_orchestration.env.compute_identity_hash", lambda **kw: "x"
    )
    monkeypatch.setattr(
        "prefect_orchestration.doctor._check_env_pool_worker",
        lambda pool, prefix: CheckResult(
            name=f"{prefix}: pool-worker", status=Status.OK, message="ok"
        ),
    )

    results = doctor_mod.run_env_checks()
    driver_rows = [r for r in results if r.name == "myenv: driver"]
    assert driver_rows[0].status is Status.FAIL
    assert "unreachable" in driver_rows[0].message


def test_run_env_checks_no_pool_worker(monkeypatch):
    rec = _FakeEnvRecord(
        name="myenv",
        driver="noop",
        snapshot_tag="",
        pool="po-env-myenv",
        opaque={},
        rig_remote="",
        identity_hash="x",
    )
    from prefect_orchestration.env_drivers import NoopDriver

    noop = NoopDriver()
    monkeypatch.setattr("prefect_orchestration.env.list_envs", lambda: [rec])
    monkeypatch.setattr(
        "prefect_orchestration.env_drivers.load_drivers", lambda: {"noop": noop}
    )
    monkeypatch.setattr(
        "prefect_orchestration.env.compute_identity_hash", lambda **kw: "x"
    )
    monkeypatch.setattr(
        "prefect_orchestration.doctor._check_env_pool_worker",
        lambda pool, prefix: CheckResult(
            name=f"{prefix}: pool-worker",
            status=Status.FAIL,
            message="none online",
            remediation="prefect worker start",
        ),
    )

    results = doctor_mod.run_env_checks()
    pool_rows = [r for r in results if r.name == "myenv: pool-worker"]
    assert pool_rows[0].status is Status.FAIL


def test_run_env_checks_git_push_fail(monkeypatch):
    rec = _FakeEnvRecord(
        name="myenv",
        driver="noop",
        snapshot_tag="",
        pool="po-env-myenv",
        opaque={},
        rig_remote="git@host:repo.git",
        identity_hash="x",
    )
    from prefect_orchestration.env_drivers import NoopDriver

    class _FakeProc:
        returncode = 128
        stderr = "fatal: no such remote"
        stdout = ""

    noop = NoopDriver()
    monkeypatch.setattr("prefect_orchestration.env.list_envs", lambda: [rec])
    monkeypatch.setattr(
        "prefect_orchestration.env_drivers.load_drivers", lambda: {"noop": noop}
    )
    monkeypatch.setattr(
        "prefect_orchestration.env.compute_identity_hash", lambda **kw: "x"
    )
    monkeypatch.setattr(
        "prefect_orchestration.doctor._check_env_pool_worker",
        lambda pool, prefix: CheckResult(
            name=f"{prefix}: pool-worker", status=Status.OK, message="ok"
        ),
    )
    monkeypatch.setattr(doctor_mod.subprocess, "run", lambda *a, **k: _FakeProc())

    results = doctor_mod.run_env_checks()
    git_rows = [r for r in results if r.name == "myenv: git-push"]
    assert git_rows[0].status is Status.FAIL
    assert "128" in git_rows[0].message


# -- cron check ---------------------------------------------------------


@dataclass
class _FakeCronDep:
    name: str = "morning-pr-review"
    flow_name: str = "software-dev-fast"
    work_pool_name: str | None = "po"


def _fake_cron_builder(deps: list[_FakeCronDep]):
    def _build(orders_dir, **kwargs):
        return deps

    return _build


def test_cron_check_no_orders_dir(tmp_path):
    missing = tmp_path / "nope"
    results = doctor_mod.run_cron_checks(missing)
    assert len(results) == 1
    assert results[0].status is Status.OK
    assert "skipping" in results[0].message


def test_cron_check_empty_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        doctor_mod._deployments,
        "build_cron_deployments_from_order_dir",
        _fake_cron_builder([]),
    )
    results = doctor_mod.run_cron_checks(tmp_path)
    assert len(results) == 1
    assert results[0].status is Status.OK
    assert "no cron deployments" in results[0].message


def test_cron_check_declared_not_live_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(
        doctor_mod._deployments,
        "build_cron_deployments_from_order_dir",
        _fake_cron_builder([_FakeCronDep()]),
    )
    monkeypatch.setattr(
        doctor_mod._deployments, "format_schedule", lambda d: "cron(0 9 * * 1-5)"
    )
    monkeypatch.setattr(doctor_mod, "_read_live_deployment_names", lambda: set())
    monkeypatch.setattr(
        doctor_mod,
        "_check_env_pool_worker",
        lambda pool, prefix: CheckResult(
            name=f"{prefix}: pool-worker", status=Status.OK, message="ok"
        ),
    )
    results = doctor_mod.run_cron_checks(tmp_path)
    cron_rows = [r for r in results if r.name.startswith("cron: ")]
    assert cron_rows[0].status is Status.WARN
    assert "declared" in cron_rows[0].message
    assert "po cron apply" in cron_rows[0].remediation


def test_cron_check_live_is_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(
        doctor_mod._deployments,
        "build_cron_deployments_from_order_dir",
        _fake_cron_builder([_FakeCronDep()]),
    )
    monkeypatch.setattr(
        doctor_mod._deployments, "format_schedule", lambda d: "cron(0 9 * * 1-5)"
    )
    monkeypatch.setattr(
        doctor_mod,
        "_read_live_deployment_names",
        lambda: {"software-dev-fast/morning-pr-review"},
    )
    monkeypatch.setattr(
        doctor_mod,
        "_check_env_pool_worker",
        lambda pool, prefix: CheckResult(
            name=f"{prefix}: pool-worker", status=Status.OK, message="ok"
        ),
    )
    results = doctor_mod.run_cron_checks(tmp_path)
    cron_rows = [r for r in results if r.name.startswith("cron: ")]
    assert cron_rows[0].status is Status.OK
    assert "live" in cron_rows[0].message
    # one pool-worker row for the single pinned pool
    pool_rows = [r for r in results if "pool-worker" in r.name]
    assert len(pool_rows) == 1


def test_cron_check_server_unknown_when_api_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("PREFECT_API_URL", raising=False)
    monkeypatch.setattr(
        doctor_mod._deployments,
        "build_cron_deployments_from_order_dir",
        _fake_cron_builder([_FakeCronDep()]),
    )
    monkeypatch.setattr(
        doctor_mod._deployments, "format_schedule", lambda d: "cron(0 9 * * 1-5)"
    )
    monkeypatch.setattr(
        doctor_mod,
        "_check_env_pool_worker",
        lambda pool, prefix: CheckResult(
            name=f"{prefix}: pool-worker",
            status=Status.WARN,
            message="PREFECT_API_URL not set; skipping",
        ),
    )
    results = doctor_mod.run_cron_checks(tmp_path)
    cron_rows = [r for r in results if r.name.startswith("cron: ")]
    # API unreachable -> server state unknown, row stays OK (not a false WARN)
    assert cron_rows[0].status is Status.OK
    assert "unknown" in cron_rows[0].message


def test_doctor_cli_check_cron(monkeypatch, hide_pack_doctor_checks):
    monkeypatch.setattr(
        doctor_mod,
        "run_cron_checks",
        lambda orders_dir: [
            CheckResult(name="cron orders", status=Status.OK, message="no orders dir")
        ],
    )
    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--check=cron"])
    assert result.exit_code == 0
    assert "SOURCE" in result.output
    assert "cron orders" in result.output


def test_doctor_cli_check_unknown_lists_cron(hide_pack_doctor_checks):
    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--check=bogus"])
    assert result.exit_code == 1
    assert "cron" in result.output


def test_doctor_cli_check_envs(monkeypatch, hide_pack_doctor_checks):
    monkeypatch.setattr(
        doctor_mod,
        "run_env_checks",
        lambda: [
            CheckResult(
                name="registered envs", status=Status.OK, message="no envs registered"
            )
        ],
    )
    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--check=envs"])
    assert result.exit_code == 0
    assert "SOURCE" in result.output
    assert "registered envs" in result.output
