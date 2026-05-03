"""Unit tests for `prefect_orchestration.role_registry`."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

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


def test_select_backend_factory_env_codex_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from prefect_orchestration.agent_session import CodexCliBackend

    monkeypatch.setenv("PO_BACKEND", "codex-cli")
    assert _select_backend_factory(dry_run=False) is CodexCliBackend


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
        formula_name="software-dev-full",
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


def test_build_registry_threads_rig_path_through_every_bd_shellout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC2 (prefect-orchestration-3mw): every bd shellout `build_registry`
    issues — `_resolve_pack_path` show, the inline run-location stamp,
    `_resolve_tmux_scope` show, and `claim_issue` — must carry
    `cwd=str(rig_path)`.

    Also confirms the constructed `BeadsStore` carries `rig_path` so
    later `store.get` / `.set` calls inherit the cwd routing.

    `stamp_run_url_on_bead`'s cwd plumbing is exercised by a focused
    test below (in test environments `flow_run.get_id()` returns None,
    so fr_id falls back to "local" and stamp_run_url short-circuits at
    URL composition — which is also the natural production path when
    no Prefect server is reachable).
    """
    rig_path = tmp_path.resolve()

    # Pretend bd is on PATH everywhere shutil.which is checked.
    monkeypatch.setattr(
        "prefect_orchestration.role_registry.shutil.which",
        lambda _name: "/usr/bin/bd",
    )
    monkeypatch.setattr(
        "prefect_orchestration.beads_meta.shutil.which",
        lambda _name: "/usr/bin/bd",
    )
    monkeypatch.setattr(
        "prefect_orchestration.run_handles.shutil.which",
        lambda _name: "/usr/bin/bd",
    )

    bd_calls: list[tuple[list[str], Any]] = []

    def _record(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess:
        bd_calls.append((list(cmd), kw.get("cwd")))
        # `_resolve_pack_path` and `_resolve_tmux_scope` read bd show
        # output for metadata fields; return an empty dict so they fall
        # through their default branches.
        if cmd[:2] == ["bd", "show"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='{"id":"x","metadata":{}}',
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    # Patch every module that issues bd shellouts during build_registry.
    monkeypatch.setattr("prefect_orchestration.role_registry.subprocess.run", _record)
    monkeypatch.setattr("prefect_orchestration.beads_meta.subprocess.run", _record)
    monkeypatch.setattr("prefect_orchestration.run_handles.subprocess.run", _record)

    reg, _ctx = build_registry(
        issue_id="i-1",
        rig="r",
        rig_path=str(rig_path),
        agents_dir=tmp_path / "agents",
        formula_name="test-formula",
        parent_bead="EPIC",
        dry_run=False,
        roles=("triager",),
    )

    bd_only = [(cmd, cwd) for cmd, cwd in bd_calls if cmd[:1] == ["bd"]]
    assert bd_only, "expected at least one bd shellout from build_registry"
    # subprocess accepts both `str` and `Path` for `cwd`; normalise via str().
    bad = [
        (cmd, cwd) for cmd, cwd in bd_only if cwd is None or str(cwd) != str(rig_path)
    ]
    assert not bad, (
        f"every bd shellout must carry cwd={str(rig_path)!r}; these did not: {bad}"
    )

    # Each of the four expected shellout sites should have fired at least
    # once. Match by the leading verb + a distinguishing flag.
    cmds = [cmd for cmd, _ in bd_only]
    # _resolve_pack_path: `bd show <issue> --json`
    assert any(c[:2] == ["bd", "show"] and "--json" in c for c in cmds), (
        "expected _resolve_pack_path bd show shellout"
    )
    # inline run-location stamp: `bd update <issue> --set-metadata po.rig_path=...`
    assert any(
        c[:2] == ["bd", "update"]
        and any(isinstance(a, str) and a.startswith("po.rig_path=") for a in c)
        for c in cmds
    ), "expected inline run-location-stamp bd update shellout"
    # claim_issue: `bd update <issue> --status in_progress --assignee ...`
    assert any(
        c[:2] == ["bd", "update"] and "--status" in c and "in_progress" in c
        for c in cmds
    ), "expected claim_issue bd update shellout"

    # Constructed store should be a BeadsStore (bd "available") carrying rig_path.
    from prefect_orchestration.beads_meta import BeadsStore

    assert isinstance(reg.store, BeadsStore)
    assert reg.store.rig_path == rig_path


def test_stamp_run_url_on_bead_passes_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC2 (prefect-orchestration-3mw): `stamp_run_url_on_bead` threads
    rig_path into its `bd update` shellout."""
    from prefect_orchestration.run_handles import stamp_run_url_on_bead

    monkeypatch.setattr(
        "prefect_orchestration.run_handles.shutil.which",
        lambda _name: "/usr/bin/bd",
    )
    monkeypatch.setenv("PREFECT_API_URL", "http://127.0.0.1:4200/api")

    calls: list[tuple[list[str], Any]] = []

    def _record(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess:
        calls.append((list(cmd), kw.get("cwd")))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("prefect_orchestration.run_handles.subprocess.run", _record)

    stamp_run_url_on_bead("i-1", "abc12345-stable", rig_path=tmp_path)
    assert calls, "expected one bd update shellout"
    cmd, cwd = calls[0]
    assert cmd[:3] == ["bd", "update", "i-1"]
    assert cwd == str(tmp_path)


def test_seed_inheritance_across_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC (a): two RoleRegistry instances on different children of the same
    seed share the role's session uuid via the seed-keyed RoleSessionStore.

    Uses a no-bd setup so writes land in `<seed_run_dir>/role-sessions.json`,
    which both child registries can read deterministically.
    """
    from prefect_orchestration.beads_meta import FileStore
    from prefect_orchestration.role_sessions import RoleSessionStore

    seed_run = tmp_path / "seed"
    # Child 1: persists builder uuid
    legacy_c1 = tmp_path / "C1"
    legacy_c1.mkdir()
    store_c1 = RoleSessionStore(
        seed_id="P", seed_run_dir=seed_run, legacy_self_run_dir=legacy_c1
    )
    reg1 = RoleRegistry(
        rig_path=tmp_path,
        store=FileStore(path=legacy_c1 / "metadata.json"),
        backend_factory=StubBackend,
        issue_id="C1",
        role_session_store=store_c1,
    )
    sess1 = reg1.get("builder")
    sess1.session_id = "uuid-shared-builder"
    reg1.persist("builder")

    # Child 2: fresh registry pointed at same seed; should *inherit* uuid.
    legacy_c2 = tmp_path / "C2"
    legacy_c2.mkdir()
    store_c2 = RoleSessionStore(
        seed_id="P", seed_run_dir=seed_run, legacy_self_run_dir=legacy_c2
    )
    reg2 = RoleRegistry(
        rig_path=tmp_path,
        store=FileStore(path=legacy_c2 / "metadata.json"),
        backend_factory=StubBackend,
        issue_id="C2",
        role_session_store=store_c2,
    )
    sess2 = reg2.get("builder")
    assert sess2.session_id == "uuid-shared-builder"


def test_persist_to_new_seed_does_not_pollute_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC (b): `persist_to(role, branch_seed)` writes only to the branch
    seed; the registry's bound seed is untouched, preserving the
    inheritance chain on the original branch."""
    from prefect_orchestration.beads_meta import FileStore
    from prefect_orchestration.role_sessions import RoleSessionStore

    # Original seed: P; branch seed: B (shares the formula dir).
    formula_dir = tmp_path / ".planning" / "software-dev-full"
    formula_dir.mkdir(parents=True)
    p_seed = formula_dir / "P"
    b_seed = formula_dir / "B"

    legacy = tmp_path / ".planning" / "software-dev-full" / "C1"
    legacy.mkdir()

    store = RoleSessionStore(
        seed_id="P", seed_run_dir=p_seed, legacy_self_run_dir=legacy
    )
    reg = RoleRegistry(
        rig_path=tmp_path,
        store=FileStore(path=legacy / "metadata.json"),
        backend_factory=StubBackend,
        issue_id="C1",
        role_session_store=store,
    )
    # Pre-populate original seed with the pre-fork uuid.
    sess = reg.get("critic")
    sess.session_id = "pre-fork"
    reg.persist("critic")
    # Now simulate a forked turn yielding a new uuid.
    sess.session_id = "post-fork"
    reg.persist_to("critic", seed_id="B")

    # Branch seed has the post-fork uuid; original seed retains pre-fork.
    branch_store = RoleSessionStore(seed_id="B", seed_run_dir=b_seed)
    assert branch_store.get("critic") == "post-fork"
    # Re-read the original seed — must not be polluted by the branch write.
    original_store = RoleSessionStore(seed_id="P", seed_run_dir=p_seed)
    assert original_store.get("critic") == "pre-fork"


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
