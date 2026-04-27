"""Per-role Claude session registry + bootstrap factory.

Lifted from `po_formulas.software_dev` so any pack can build its own
formula on the same scaffolding without copy-paste. Behavior is identical
to the original inline implementation; only generalization is that
`roles` (used for `links.md`) and `code_roles` (cwd-routing for code-
editing roles) are now dataclass fields instead of pack-level constants.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prefect.runtime import flow_run

from prefect_orchestration.agent_session import (
    AgentSession,
    ClaudeCliBackend,
    StubBackend,
    TmuxClaudeBackend,
    TmuxInteractiveClaudeBackend,
)
from prefect_orchestration.beads_meta import MetadataStore, auto_store, claim_issue
from prefect_orchestration.role_artifacts import publish_role_artifacts
from prefect_orchestration.run_handles import stamp_run_url_on_bead, write_run_handles

_DEFAULT_CODE_ROLES: frozenset[str] = frozenset({"builder", "linter", "cleaner"})


@dataclass
class RoleRegistry:
    rig_path: Path
    store: MetadataStore
    backend_factory: Any = field(default=ClaudeCliBackend)
    issue_id: str = ""
    run_dir: Path | None = None
    flow_run_id: str | None = None
    # Where code edits + git ops happen for code-editing roles.
    # `None` (the default) means "same as rig_path" — i.e. the bead repo
    # is also the code repo. Set explicitly to split rig from pack.
    code_path: Path | None = None
    # Tmux scope: groups concurrent role spawns into one shared session.
    # `f"{rig}-{epic}"` for epic children, `rig` for solo runs. Plumbed
    # to tmux backends via `_make_backend`. None means use legacy layout
    # (one top-level tmux session per (issue, role)).
    tmux_scope: str | None = None
    # Roles this formula uses — drives `links.md` rendering. Pack-supplied
    # so core stays formula-agnostic.
    roles: tuple[str, ...] = ()
    # Roles whose AgentSession cwd should follow `code_path` rather than
    # `rig_path`. Default covers the SDF case (`builder`/`linter`/`cleaner`);
    # other packs can override.
    code_roles: frozenset[str] = field(default_factory=lambda: _DEFAULT_CODE_ROLES)
    _sessions: dict[str, AgentSession] = field(default_factory=dict)

    def _make_backend(self, role: str) -> Any:
        # Tmux backends accept (issue, role[, scope]); stateless backends don't.
        if self.tmux_scope is not None:
            try:
                return self.backend_factory(
                    issue=self.issue_id, role=role, scope=self.tmux_scope
                )
            except TypeError:
                pass
        try:
            return self.backend_factory(issue=self.issue_id, role=role)
        except TypeError:
            return self.backend_factory()

    def _cwd_for_role(self, role: str) -> Path:
        if self.code_path is not None and role in self.code_roles:
            return self.code_path
        return self.rig_path

    def get(self, role: str) -> AgentSession:
        if role not in self._sessions:
            sid = self.store.get(f"session_{role}")
            self._sessions[role] = AgentSession(
                role=role,
                repo_path=self._cwd_for_role(role),
                backend=self._make_backend(role),
                session_id=sid,
            )
        return self._sessions[role]

    def persist(self, role: str) -> None:
        sess = self._sessions.get(role)
        if sess and sess.session_id:
            self.store.set(f"session_{role}", sess.session_id)
        # Refresh links.md so the user always sees the latest UUIDs.
        if self.run_dir is not None:
            self._refresh_handles()

    def publish(
        self,
        role: str,
        iter_n: int,
        output_files: list[str],
    ) -> None:
        """Surface this role's file outputs + Claude transcript on the Prefect run page.

        Best-effort wrapper around `publish_role_artifacts` that
        resolves the role's current session UUID from the in-memory
        session registry. Safe to call for tasks that produce no
        canonical files (`output_files=[]`) — only the transcript link
        will be emitted.
        """
        if self.run_dir is None:
            return
        sess = self._sessions.get(role)
        sid = sess.session_id if sess else None
        publish_role_artifacts(
            run_dir=self.run_dir,
            rig_path=self.rig_path,
            role=role,
            iter_n=iter_n,
            session_id=sid,
            output_files=output_files,
            issue_id=self.issue_id,
            tmux_scope=self.tmux_scope,
        )

    def _refresh_handles(self) -> None:
        sessions = {
            role: self.store.get(f"session_{role}")
            for role in self.roles
            if self.store.get(f"session_{role}")
        }
        prefix = f"po-{self.issue_id.replace('.', '_')}"
        write_run_handles(
            issue_id=self.issue_id,
            run_dir=self.run_dir,  # type: ignore[arg-type]
            flow_run_id=self.flow_run_id,
            roles=self.roles,
            sessions=sessions,
            tmux_session_prefix=prefix,
            tmux_scope=self.tmux_scope,
            tmux_window_issue=self.issue_id if self.tmux_scope else None,
            rig_path=self.rig_path,
        )


def _resolve_pack_path(
    explicit: str | None,
    issue_id: str,
    rig_path_p: Path,
) -> Path:
    """Pick the pack_path: CLI explicit > bd metadata `po.target_pack` > rig_path.

    The bd metadata lookup queries the issue itself (not its parent epic),
    so `bd update <id> --set-metadata po.target_pack=/abs/path` on a single
    bead overrides the default for that one issue without a CLI change.
    """
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    if shutil.which("bd") is not None:
        proc = subprocess.run(
            ["bd", "show", issue_id, "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            try:
                import json as _json

                data = _json.loads(proc.stdout)
                row = data[0] if isinstance(data, list) else data
                meta = row.get("metadata") or {}
                target = meta.get("po.target_pack")
                if target:
                    return Path(str(target)).expanduser().resolve()
            except (ValueError, KeyError, IndexError):
                pass
    return rig_path_p


def _select_backend_factory(dry_run: bool) -> Any:
    """Pick the AgentSession backend factory from PO_BACKEND + tmux availability.

    Mirrors the inline switch previously at the top of `software_dev_full`.
    """
    if dry_run:
        return StubBackend
    choice = os.environ.get("PO_BACKEND", "").lower()
    if choice == "cli":
        return ClaudeCliBackend
    if choice == "stub":
        return StubBackend
    if choice == "tmux-stream":
        if shutil.which("tmux") is None:
            raise RuntimeError("PO_BACKEND=tmux-stream but tmux not on PATH")
        return TmuxClaudeBackend
    if choice == "tmux":
        if shutil.which("tmux") is None:
            raise RuntimeError("PO_BACKEND=tmux but tmux not on PATH")
        return TmuxInteractiveClaudeBackend
    return TmuxInteractiveClaudeBackend if shutil.which("tmux") else ClaudeCliBackend


def _resolve_tmux_scope(
    rig: str,
    issue_id: str,
    parent_bead: str | None,
    rig_path_p: Path,
    dry_run: bool,
) -> str:
    """Compute tmux scope: `{rig}-{epic}` for epic children, else `{rig}`."""
    epic_for_scope = parent_bead
    if epic_for_scope is None and not dry_run and shutil.which("bd") is not None:
        try:
            res = subprocess.run(
                ["bd", "show", issue_id, "--json"],
                capture_output=True,
                check=False,
                text=True,
                cwd=rig_path_p,
            )
            if res.returncode == 0 and res.stdout.strip():
                import json as _json

                meta = _json.loads(res.stdout)
                for k in ("parent", "epic", "epic_id", "parent_id"):
                    val = meta.get(k) if isinstance(meta, dict) else None
                    if isinstance(val, str) and val and val != issue_id:
                        epic_for_scope = val
                        break
        except Exception:  # noqa: BLE001
            pass
    return f"{rig}-{epic_for_scope}" if epic_for_scope else rig


def build_registry(
    issue_id: str,
    rig: str,
    rig_path: str,
    agents_dir: Path,
    *,
    pack_path: str | None = None,
    parent_bead: str | None = None,
    dry_run: bool = False,
    claim: bool = True,
    roles: tuple[str, ...] = (),
    code_roles: frozenset[str] | None = None,
    formula_name: str = "software-dev-full",
) -> tuple[RoleRegistry, dict[str, Any]]:
    """Bundle the standard PO flow bootstrap into one call.

    Resolves rig/pack paths, creates the run_dir + verdicts/ tree, picks
    a backend factory from PO_BACKEND, opens a beads `MetadataStore`,
    constructs the `RoleRegistry`, tags the Prefect flow run with
    `issue_id:<id>`, seeds `links.md`, stamps the run URL on the bead,
    and (unless `dry_run`) claims the issue.

    Returns `(reg, base_ctx)` where `base_ctx` carries the variables
    every role prompt expects (`issue_id`, `rig`, `rig_path`,
    `pack_path`, `run_dir` — all string-valued).

    `agents_dir` is the pack's `agents/` directory; not used directly
    here but accepted to match the per-formula bootstrap API and keep
    the call site self-documenting. `formula_name` controls the run-dir
    layout (`<rig_path>/.planning/<formula_name>/<issue_id>/`).
    """
    del agents_dir  # placeholder for API symmetry; runs may need it later

    rig_path_p = Path(rig_path).expanduser().resolve()
    run_dir = rig_path_p / ".planning" / formula_name / issue_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "verdicts").mkdir(exist_ok=True)

    pack_path_p = _resolve_pack_path(pack_path, issue_id, rig_path_p)

    # Stamp run location on the bead so `po logs/artifacts/sessions/retry/watch`
    # can resolve issue → run_dir later. Best-effort.
    if not dry_run and shutil.which("bd") is not None:
        subprocess.run(
            [
                "bd", "update", issue_id,
                "--set-metadata", f"po.rig_path={rig_path_p}",
                "--set-metadata", f"po.run_dir={run_dir}",
                "--set-metadata", f"po.pack_path={pack_path_p}",
            ],
            check=False,
        )

    store = auto_store(parent_bead, run_dir)
    backend_factory = _select_backend_factory(dry_run)
    fr_id = flow_run.get_id() or "local"
    tmux_scope = _resolve_tmux_scope(rig, issue_id, parent_bead, rig_path_p, dry_run)

    reg = RoleRegistry(
        rig_path=rig_path_p,
        store=store,
        backend_factory=backend_factory,
        issue_id=issue_id,
        run_dir=run_dir,
        flow_run_id=fr_id,
        code_path=pack_path_p if pack_path_p != rig_path_p else None,
        tmux_scope=tmux_scope,
        roles=roles,
        code_roles=code_roles if code_roles is not None else _DEFAULT_CODE_ROLES,
    )

    # Tag the flow run with `issue_id:<id>` so `po status` can group by
    # bead. Best-effort — Prefect client hiccup must not abort the flow.
    if fr_id != "local":
        try:
            from prefect.client.orchestration import get_client

            with get_client(sync_client=True) as _c:
                existing = list(flow_run.tags or [])
                new_tag = f"issue_id:{issue_id}"
                if new_tag not in existing:
                    _c.update_flow_run(fr_id, tags=[*existing, new_tag])
        except Exception:  # noqa: BLE001
            pass

    reg._refresh_handles()
    stamp_run_url_on_bead(issue_id, fr_id, dry_run=dry_run)

    if claim and not dry_run:
        claim_issue(issue_id, assignee=f"po-{fr_id[:8]}")

    base_ctx: dict[str, Any] = {
        "issue_id": issue_id,
        "rig": rig,
        "rig_path": str(rig_path_p),
        "pack_path": str(pack_path_p),
        "run_dir": str(run_dir),
    }
    return reg, base_ctx


__all__ = ["RoleRegistry", "build_registry"]
