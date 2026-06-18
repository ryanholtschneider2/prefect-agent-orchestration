"""CLI-level tests for the pack lifecycle verbs."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prefect_orchestration import packs
from prefect_orchestration.cli import app


runner = CliRunner()


def _fake_ok(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    called: list[list[str]] = []

    def fake(args: list[str]) -> subprocess.CompletedProcess[str]:
        called.append(args)
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(packs, "_run_uv", fake)
    return called


def test_install_cli_passes_spec_to_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    called = _fake_ok(monkeypatch)
    result = runner.invoke(app, ["packs", "install", "po-formulas-software-dev"])
    assert result.exit_code == 0, result.output
    assert "installed po-formulas-software-dev" in result.output
    assert called and called[0][-1] == "po-formulas-software-dev"
    assert "--with" in called[0]


def test_install_editable_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    called = _fake_ok(monkeypatch)
    result = runner.invoke(app, ["packs", "install", "--editable", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert called[0][-2] == "--with-editable"
    assert called[0][-1] == str(tmp_path)


def test_update_all(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_ok(monkeypatch)
    pi = packs.PackInfo(
        name="po-formulas-x",
        version="0.1",
        source="pypi",
        contributions={"po.formulas": ["y"]},
    )
    monkeypatch.setattr(packs, "discover_packs", lambda: [pi])
    result = runner.invoke(app, ["packs", "update"])
    assert result.exit_code == 0, result.output
    assert "po-formulas-x" in result.output


def test_uninstall_refuses_self(monkeypatch: pytest.MonkeyPatch) -> None:
    result = runner.invoke(app, ["packs", "uninstall", packs.CORE_DISTRIBUTION])
    assert result.exit_code == 2
    assert "refusing" in result.output


def test_uninstall_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_ok(monkeypatch)
    monkeypatch.setattr(packs, "discover_packs", lambda: [])
    result = runner.invoke(app, ["packs", "uninstall", "po-formulas-foo"])
    assert result.exit_code == 0, result.output
    assert "uninstalled po-formulas-foo" in result.output


def test_packs_lists_contributions(monkeypatch: pytest.MonkeyPatch) -> None:
    pi = packs.PackInfo(
        name="po-formulas-x",
        version="0.1",
        source="pypi",
        contributions={"po.formulas": ["flow-a"], "po.commands": ["cmd-b"]},
    )
    monkeypatch.setattr(packs, "discover_packs", lambda: [pi])
    result = runner.invoke(app, ["packs", "list"])
    assert result.exit_code == 0, result.output
    assert "po-formulas-x" in result.output
    assert "formulas=flow-a" in result.output
    assert "commands=cmd-b" in result.output


def test_install_missing_uv_prints_pointer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(packs.shutil, "which", lambda _n: None)
    result = runner.invoke(app, ["packs", "install", "po-pack"])
    assert result.exit_code == 2
    assert "astral.sh/uv" in result.output


def test_install_copies_overlay_to_rig_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_ok(monkeypatch)
    from prefect_orchestration.pack_overlay import Pack

    pack_root = tmp_path / "po-mypack"
    pack_root.mkdir()
    overlay_dir = pack_root / "overlay"
    overlay_dir.mkdir()
    (overlay_dir / "CLAUDE-mypack.md").write_text("# mypack")
    module_root = pack_root / "po_mypack"
    module_root.mkdir()
    (module_root / "__init__.py").write_text("")
    fake_pack = Pack(name="po-mypack", root=pack_root, module_root=module_root)

    import prefect_orchestration.pack_overlay as po_mod

    monkeypatch.setattr(po_mod, "discover_packs", lambda: [fake_pack])

    rig = tmp_path / "rig"
    rig.mkdir()
    result = runner.invoke(
        app, ["packs", "install", "po-mypack", "--rig-path", str(rig)]
    )
    assert result.exit_code == 0, result.output
    assert (rig / ".claude" / "packs" / "CLAUDE-mypack.md").exists()
    assert "overlay ->" in result.output


def test_install_no_overlay_no_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_ok(monkeypatch)
    from prefect_orchestration.pack_overlay import Pack

    pack_root = tmp_path / "po-nooverlay"
    pack_root.mkdir()
    module_root = pack_root / "po_nooverlay"
    module_root.mkdir()
    (module_root / "__init__.py").write_text("")
    fake_pack = Pack(name="po-nooverlay", root=pack_root, module_root=module_root)

    import prefect_orchestration.pack_overlay as po_mod

    monkeypatch.setattr(po_mod, "discover_packs", lambda: [fake_pack])

    rig = tmp_path / "rig"
    rig.mkdir()
    result = runner.invoke(
        app, ["packs", "install", "po-nooverlay", "--rig-path", str(rig)]
    )
    assert result.exit_code == 0, result.output
    assert "overlay ->" not in result.output


# -- deployment application on packs install ----------------------------------


def _stub_pack(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Monkeypatch pack discovery to return a no-overlay pack (overlay side-effect free)."""
    from prefect_orchestration.pack_overlay import Pack

    pack_root = tmp_path / "po-stub"
    pack_root.mkdir()
    module_root = pack_root / "po_stub"
    module_root.mkdir()
    (module_root / "__init__.py").write_text("")
    fake_pack = Pack(name="po-stub", root=pack_root, module_root=module_root)
    import prefect_orchestration.pack_overlay as po_mod

    monkeypatch.setattr(po_mod, "discover_packs", lambda: [fake_pack])


def test_install_applies_deployments_when_api_url_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_ok(monkeypatch)
    _stub_pack(monkeypatch, tmp_path)
    monkeypatch.setenv("PREFECT_API_URL", "http://127.0.0.1:4200/api")

    applied_rigs: list[Path] = []

    from prefect_orchestration import deployments as dep_mod

    def fake_apply_rig(rig_path, *, work_pool=None, **k):
        applied_rigs.append(rig_path)
        result = dep_mod.RigDeploymentResult(
            pack="mypack",
            deployment_name=f"nightly-{rig_path.name}",
            deployment_id="uuid-abc",
        )
        return [result], []

    monkeypatch.setattr(dep_mod, "apply_rig_deployments", fake_apply_rig)

    rig = tmp_path / "my-rig"
    rig.mkdir()
    result = runner.invoke(
        app, ["packs", "install", "po-mypack", "--rig-path", str(rig)]
    )
    assert result.exit_code == 0, result.output
    assert applied_rigs == [rig]
    assert f"deployment -> nightly-{rig.name}" in result.output


def test_install_deployment_errors_printed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_ok(monkeypatch)
    _stub_pack(monkeypatch, tmp_path)
    monkeypatch.setenv("PREFECT_API_URL", "http://127.0.0.1:4200/api")

    from prefect_orchestration import deployments as dep_mod

    def fake_apply_rig(rig_path, **k):
        err = dep_mod.RigDeploymentResult(
            pack="bad", deployment_name="nightly-rig", error="server down"
        )
        return [], [err]

    monkeypatch.setattr(dep_mod, "apply_rig_deployments", fake_apply_rig)

    rig = tmp_path / "rig"
    rig.mkdir()
    result = runner.invoke(
        app, ["packs", "install", "po-mypack", "--rig-path", str(rig)]
    )
    assert result.exit_code == 0  # non-fatal
    assert "deployment error" in result.output


def test_install_hints_when_no_api_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_ok(monkeypatch)
    _stub_pack(monkeypatch, tmp_path)
    monkeypatch.delenv("PREFECT_API_URL", raising=False)

    from prefect_orchestration import deployments as dep_mod

    def boom(*a, **k):
        raise AssertionError("apply_rig_deployments must not be called without API URL")

    monkeypatch.setattr(dep_mod, "apply_rig_deployments", boom)

    rig = tmp_path / "rig"
    rig.mkdir()
    result = runner.invoke(
        app, ["packs", "install", "po-mypack", "--rig-path", str(rig)]
    )
    assert result.exit_code == 0, result.output
    assert "PREFECT_API_URL" in result.output


def test_install_no_deploy_flag_skips_deployments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_ok(monkeypatch)
    _stub_pack(monkeypatch, tmp_path)
    monkeypatch.setenv("PREFECT_API_URL", "http://127.0.0.1:4200/api")

    from prefect_orchestration import deployments as dep_mod

    def boom(*a, **k):
        raise AssertionError(
            "apply_rig_deployments must not be called with --no-deploy"
        )

    monkeypatch.setattr(dep_mod, "apply_rig_deployments", boom)

    rig = tmp_path / "rig"
    rig.mkdir()
    result = runner.invoke(
        app,
        ["packs", "install", "po-mypack", "--rig-path", str(rig), "--no-deploy"],
    )
    assert result.exit_code == 0, result.output


def test_install_work_pool_passed_to_deployments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_ok(monkeypatch)
    _stub_pack(monkeypatch, tmp_path)
    monkeypatch.setenv("PREFECT_API_URL", "http://127.0.0.1:4200/api")

    from prefect_orchestration import deployments as dep_mod

    received_pool: list[str | None] = []

    def fake_apply_rig(rig_path, *, work_pool=None, **k):
        received_pool.append(work_pool)
        return [], []

    monkeypatch.setattr(dep_mod, "apply_rig_deployments", fake_apply_rig)

    rig = tmp_path / "rig"
    rig.mkdir()
    runner.invoke(
        app,
        [
            "packs",
            "install",
            "po-mypack",
            "--rig-path",
            str(rig),
            "--work-pool",
            "custom-pool",
        ],
    )
    assert received_pool == ["custom-pool"]


def test_install_without_rig_path_does_not_apply_deployments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without --rig-path, deployments are not applied (cwd-only overlay mode)."""
    _fake_ok(monkeypatch)
    _stub_pack(monkeypatch, tmp_path)
    monkeypatch.setenv("PREFECT_API_URL", "http://127.0.0.1:4200/api")

    from prefect_orchestration import deployments as dep_mod

    def boom(*a, **k):
        raise AssertionError(
            "apply_rig_deployments must not be called without --rig-path"
        )

    monkeypatch.setattr(dep_mod, "apply_rig_deployments", boom)

    result = runner.invoke(app, ["packs", "install", "po-mypack"])
    assert result.exit_code == 0, result.output
