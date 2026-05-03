"""po formula: `prompt` — dispatch a single Claude agent with one prompt.

Single-turn agent dispatch — no actor-critic loop. Auto-creates a
`po-prompt`-labeled bead so all `po` commands (status / watch / artifacts
/ sessions / retry / logs) work uniformly. Bead context is injected into
the prompt so the agent self-manages notes and follow-ups.

Lurkable via tmux when tmux is on PATH (default backend) — attach with
`tmux attach -t po-<bd_id>-<role>`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from prefect import flow, get_run_logger
from prefect.artifacts import create_markdown_artifact
from prefect.runtime import flow_run

from prefect_orchestration.agent_session import (
    AgentSession,
    ClaudeCliBackend,
    CodexCliBackend,
    StubBackend,
    TmuxClaudeBackend,
    TmuxCodexBackend,
    TmuxInteractiveClaudeBackend,
)


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_BD_LABEL = "po-prompt"


def _slug_from_prompt(prompt: str, max_words: int = 6, max_len: int = 40) -> str:
    text = prompt.strip()
    if text.startswith("/"):
        text = text[1:]
    words = _SLUG_STRIP.sub(" ", text.lower()).split()[:max_words]
    base = "-".join(words)[:max_len].strip("-") or "prompt"
    digest = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:6]
    return f"{base}-{digest}"


def _bd_available(rig: Path) -> bool:
    return shutil.which("bd") is not None and (rig / ".beads").exists()


def _bd_create(rig: Path, slug: str, prompt: str, role: str, model: str) -> str | None:
    title = f"[po-prompt] {slug}"
    description = (
        f"Auto-created by `po run prompt`.\n\n"
        f"**Role**: {role} · **Model**: {model}\n\n"
        f"## Prompt\n\n```\n{prompt}\n```\n"
    )
    proc = subprocess.run(
        [
            "bd",
            "create",
            "--title",
            title,
            "--description",
            description,
            "--type",
            "task",
            "--priority",
            "3",
            "--label",
            _BD_LABEL,
            "--json",
        ],
        cwd=rig,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        m = re.search(r"\b([a-z0-9-]+-[a-z0-9]+)\b", proc.stdout + proc.stderr)
        return m.group(1) if m else None
    if isinstance(data, dict):
        return data.get("id") or data.get("issue_id")
    return None


def _bd_set_metadata(rig: Path, bd_id: str, **kv: str) -> None:
    args = ["bd", "update", bd_id]
    for k, v in kv.items():
        args.extend(["--set-metadata", f"{k}={v}"])
    subprocess.run(args, cwd=rig, capture_output=True, check=False)


def _bd_claim(rig: Path, bd_id: str, assignee: str) -> None:
    subprocess.run(
        ["bd", "update", bd_id, "--status", "in_progress", "--assignee", assignee],
        cwd=rig,
        capture_output=True,
        check=False,
    )


def _bd_close(rig: Path, bd_id: str, reason: str) -> None:
    subprocess.run(
        ["bd", "close", bd_id, "--reason", reason],
        cwd=rig,
        capture_output=True,
        check=False,
    )


def _pick_backend_factory(dry_run: bool) -> Any:
    if dry_run:
        return StubBackend
    choice = os.environ.get("PO_BACKEND", "").lower()
    if choice == "cli":
        return ClaudeCliBackend
    if choice == "codex-cli":
        return CodexCliBackend
    if choice == "stub":
        return StubBackend
    if choice == "tmux-stream":
        if shutil.which("tmux") is None:
            raise RuntimeError("PO_BACKEND=tmux-stream but tmux not on PATH")
        return TmuxClaudeBackend
    if choice == "codex-tmux-stream":
        if shutil.which("tmux") is None:
            raise RuntimeError("PO_BACKEND=codex-tmux-stream but tmux not on PATH")
        return TmuxCodexBackend
    if choice == "tmux":
        if shutil.which("tmux") is None:
            raise RuntimeError("PO_BACKEND=tmux but tmux not on PATH")
        return TmuxInteractiveClaudeBackend
    if choice == "codex-tmux":
        if shutil.which("tmux") is None:
            raise RuntimeError("PO_BACKEND=codex-tmux but tmux not on PATH")
        return TmuxCodexBackend
    return TmuxInteractiveClaudeBackend if shutil.which("tmux") else ClaudeCliBackend


def _make_backend(factory: Any, issue: str, role: str, scope: str | None = None) -> Any:
    """Construct a backend, plumbing `scope` when supported.

    Tmux backends accept `scope` to consolidate per-(issue, role) tmux
    sessions into a single shared session with one window per role
    spawn. Stateless backends (Stub, ClaudeCli) take no kwargs.
    """
    if scope is not None:
        try:
            return factory(issue=issue, role=role, scope=scope)
        except TypeError:
            pass
    try:
        return factory(issue=issue, role=role)
    except TypeError:
        return factory()


def _bead_prelude(bd_id: str) -> str:
    """Prefix injected before the user prompt so the agent self-manages the bead."""
    return (
        f"<po-context>\n"
        f"This run is tracked by beads issue `{bd_id}`.\n"
        f"\n"
        f"**Self-management**:\n"
        f"- Stamp progress / findings on the bead as you work:\n"
        f'  `bd update {bd_id} --notes "..."` (appends; use freely)\n'
        f"- File any unresolved follow-ups (paid data, license blockers, MOU\n"
        f"  asks, format conversions, future work) as NEW beads with\n"
        f"  `bd create --title ... --description ...` and reference\n"
        f"  `discovered-from:{bd_id}` in the description so the trail survives.\n"
        f"- The orchestrator will close `{bd_id}` automatically on success.\n"
        f"  Leave it open ONLY if you're genuinely blocked — drop a `--notes`\n"
        f"  explaining what's needed before exiting.\n"
        f"- Do NOT close `{bd_id}` yourself.\n"
        f"</po-context>\n"
        f"\n"
    )


@flow(name="prompt", flow_run_name="{label}-{role}", log_prints=True)
def prompt_run(
    prompt: str,
    rig_path: str,
    role: str = "general",
    model: str = "opus",
    label: str | None = None,
    dry_run: bool = False,
    create_bead: bool = True,
    close_on_success: bool = True,
) -> dict[str, Any]:
    """Send one prompt to one Claude agent session."""
    logger = get_run_logger()
    rig_path_p = Path(rig_path).expanduser().resolve()
    if not rig_path_p.exists():
        raise FileNotFoundError(f"rig_path does not exist: {rig_path_p}")

    label = label or _slug_from_prompt(prompt)

    bd_id: str | None = None
    if create_bead and not dry_run and _bd_available(rig_path_p):
        bd_id = _bd_create(rig_path_p, label, prompt, role, model)
        if bd_id:
            logger.info("created bead: %s", bd_id)
        else:
            logger.warning("bd create failed; falling back to slug-only handle")

    issue_handle = bd_id or f"prompt-{label}"

    run_dir = rig_path_p / ".planning" / "prompt" / issue_handle
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prompt.md").write_text(prompt)

    # Scope = rig basename so concurrent `prompt` runs against the same
    # rig collapse into one tmux session with a window per role spawn,
    # instead of N top-level sessions cluttering `tmux ls`.
    scope = rig_path_p.name
    factory = _pick_backend_factory(dry_run)
    backend = _make_backend(factory, issue=issue_handle, role=role, scope=scope)
    session = AgentSession(
        role=role, repo_path=rig_path_p, backend=backend, model=model
    )

    safe_scope = scope.replace(".", "_")
    safe_handle = issue_handle.replace(".", "_")
    safe_role = role.replace(".", "_")
    tmux_session = f"po-{safe_scope}"
    tmux_window = f"{safe_handle}-{safe_role}"
    tmux_attach = f"tmux attach -t {tmux_session} \\; select-window -t {tmux_window}"
    logger.info(
        "dispatching prompt: rig=%s role=%s model=%s handle=%s tmux=%s window=%s",
        rig_path_p,
        role,
        model,
        issue_handle,
        tmux_session,
        tmux_window,
    )

    if bd_id:
        _bd_set_metadata(
            rig_path_p,
            bd_id,
            **{"po.rig_path": str(rig_path_p), "po.run_dir": str(run_dir)},
        )

    fr_id = flow_run.get_id()
    if fr_id:
        try:
            from prefect.client.orchestration import get_client

            with get_client(sync_client=True) as _c:
                existing = list(flow_run.tags or [])
                new_tag = f"issue_id:{issue_handle}"
                if new_tag not in existing:
                    _c.update_flow_run(fr_id, tags=[*existing, new_tag])
        except Exception as exc:  # noqa: BLE001
            logger.warning("issue_id tag failed: %s", exc)

    if bd_id:
        _bd_claim(rig_path_p, bd_id, assignee=f"po-{(fr_id or 'local')[:8]}")

    # Inject bead-management context into the prompt the agent actually sees.
    effective_prompt = (_bead_prelude(bd_id) + prompt) if bd_id else prompt
    (run_dir / "prompt.md").write_text(effective_prompt)

    reply = session.prompt(effective_prompt)
    reply_path = run_dir / "reply.md"
    reply_path.write_text(reply)

    if session.session_id:
        (run_dir / "session_id.txt").write_text(session.session_id)

    create_markdown_artifact(
        key="prompt-reply",
        markdown=(
            f"### `prompt` formula reply\n\n"
            f"- **rig**: `{rig_path_p}`\n"
            f"- **role**: `{role}` · **model**: `{model}`\n"
            f"- **label**: `{label}`\n"
            f"- **bead**: `{bd_id or '(none)'}`\n"
            f"- **tmux**: `{tmux_attach}`\n"
            f"- **reply**: `{reply_path}`\n\n"
            f"---\n\n{reply[:4000]}" + ("\n\n…(truncated)" if len(reply) > 4000 else "")
        ),
    )

    if bd_id and close_on_success:
        _bd_close(rig_path_p, bd_id, reason=f"po prompt completed; reply: {reply_path}")

    return {
        "label": label,
        "bd_id": bd_id,
        "role": role,
        "run_dir": str(run_dir),
        "reply_path": str(reply_path),
        "session_id": session.session_id,
        "tmux_session": tmux_session,
        "tmux_window": tmux_window,
    }
