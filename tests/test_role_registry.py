"""Unit tests for `prefect_orchestration.role_registry`."""

from __future__ import annotations

from pathlib import Path

import pytest

from prefect_orchestration.agent_session import StubBackend
from prefect_orchestration.role_registry import (
    RoleRegistry,
    _DEFAULT_CODE_ROLES,
    _select_backend_factory,
    build_registry,
)


def test_module_exports() -> None:
    """AC1: module exists with both public symbols."""
    assert RoleRegistry is not None
    assert callable(build_registry)


def test_select_backend_factory_dry_run() -> None:
    assert _select_backend_factory(dry_run=True) is StubBackend


def test_select_backend_factory_env_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    from prefect_orchestration.agent_session import ClaudeCliBackend

    monkeypatch.setenv("PO_BACKEND", "cli")
    assert _select_backend_factory(dry_run=False) is ClaudeCliBackend


def test_build_registry_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """build_registry with dry_run=True returns (RoleRegistry, base_ctx) and
    creates the run_dir + verdicts/ subtree without shelling out to bd."""
    # Force the bd shellouts off so this test runs in any environment.
    monkeypatch.setattr(
        "prefect_orchestration.role_registry.shutil.which", lambda _: None
    )

    rig = "test-rig"
    issue_id = "test-issue-1"

    reg, ctx = build_registry(
        issue_id=issue_id,
        rig=rig,
        rig_path=str(tmp_path),
        agents_dir=tmp_path / "agents",
        dry_run=True,
        roles=("triager", "builder"),
    )

    assert isinstance(reg, RoleRegistry)
    assert reg.issue_id == issue_id
    assert reg.backend_factory is StubBackend
    assert reg.roles == ("triager", "builder")
    assert reg.code_roles == _DEFAULT_CODE_ROLES

    expected_run_dir = tmp_path / ".planning" / "software-dev-full" / issue_id
    assert expected_run_dir.is_dir()
    assert (expected_run_dir / "verdicts").is_dir()

    assert ctx == {
        "issue_id": issue_id,
        "rig": rig,
        "rig_path": str(tmp_path.resolve()),
        "pack_path": str(tmp_path.resolve()),
        "run_dir": str(expected_run_dir),
    }


def test_build_registry_custom_formula_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "prefect_orchestration.role_registry.shutil.which", lambda _: None
    )
    reg, _ctx = build_registry(
        issue_id="x",
        rig="rig",
        rig_path=str(tmp_path),
        agents_dir=tmp_path / "agents",
        dry_run=True,
        formula_name="custom-formula",
    )
    assert reg.run_dir == tmp_path / ".planning" / "custom-formula" / "x"


def test_role_registry_cwd_routing(tmp_path: Path) -> None:
    """code_roles route to code_path; non-code roles stay on rig_path."""
    from prefect_orchestration.beads_meta import FileStore

    rig = tmp_path / "rig"
    code = tmp_path / "code"
    rig.mkdir()
    code.mkdir()
    reg = RoleRegistry(
        rig_path=rig,
        store=FileStore(path=tmp_path / "meta.json"),
        backend_factory=StubBackend,
        issue_id="i",
        code_path=code,
        code_roles=frozenset({"builder"}),
    )
    assert reg._cwd_for_role("builder") == code
    assert reg._cwd_for_role("triager") == rig
