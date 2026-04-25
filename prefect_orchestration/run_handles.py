"""Per-run convenience handles: Prefect UI URL, tmux session names,
persisted Claude session UUIDs.

Every formula that runs against a beads issue benefits from one place
to find: "where's the run in the Prefect UI?", "which tmux session do
I attach to?", "what session UUID do I `--resume`?". This module owns
that. Packs call `write_run_handles()` at flow start (and again when
session UUIDs land) — the resulting `<run_dir>/links.md` is the durable
handoff to humans + future agent sessions.

Independent of the formula's role names: caller passes whatever roles
they have. Independent of the agent backend: tmux hints are skipped
when no prefix is supplied.

If/when Logfire is wired in (`prefect-orchestration-9cn`), this is the
right spot to also stamp the Logfire trace URL.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Mapping


def claude_session_jsonl(rig_path: Path, session_id: str) -> Path:
    """Path to Claude Code's local session-history JSONL for a given run.

    Claude writes per-session transcripts to
    `~/.claude/projects/<slug>/<session_id>.jsonl`, where `<slug>` is
    the cwd path with `/` replaced by `-`. This is the canonical place
    to recover what a role's agent thought, called, and said — useful
    for `claude --resume <uuid>` and for post-mortem inspection.
    """
    slug = str(rig_path.resolve()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug / f"{session_id}.jsonl"


def prefect_run_url(flow_run_id: str | None) -> str | None:
    """Compose a Prefect UI URL from PREFECT_API_URL + flow_run id.

    Returns None if no PREFECT_API_URL is set (Prefect is in ephemeral
    mode and there's no UI to point at) or if `flow_run_id` is the
    sentinel `"local"` we use when running outside Prefect.
    """
    if not flow_run_id or flow_run_id == "local":
        return None
    api = os.environ.get("PREFECT_API_URL", "").rstrip("/")
    if not api:
        return None
    base = api[: -len("/api")] if api.endswith("/api") else api
    return f"{base}/runs/flow-run/{flow_run_id}"


def stamp_run_url_on_bead(
    issue_id: str, flow_run_id: str | None, *, dry_run: bool = False
) -> None:
    """Persist the Prefect UI URL onto the bead via `bd ... --set-metadata`.

    Best-effort: skipped silently when bd is missing, in dry-run mode,
    or when no URL can be composed.
    """
    if dry_run or shutil.which("bd") is None:
        return
    url = prefect_run_url(flow_run_id)
    if not url:
        return
    subprocess.run(
        ["bd", "update", issue_id, "--set-metadata", f"po.prefect_run_url={url}"],
        check=False,
    )


def write_run_handles(
    *,
    issue_id: str,
    run_dir: Path,
    flow_run_id: str | None = None,
    roles: tuple[str, ...] = (),
    sessions: Mapping[str, str] | None = None,
    tmux_session_prefix: str | None = None,
    extra_links: Mapping[str, str] | None = None,
    rig_path: Path | None = None,
) -> Path:
    """Write `<run_dir>/links.md` summarising where to find this run.

    Idempotent — call repeatedly as new info lands (e.g. each time a
    role persists its Claude session UUID, re-invoke with an updated
    `sessions` map and the file is rewritten).

    Args:
        issue_id: bead identifier this run is keyed to.
        run_dir: per-run artifact directory; the file lands at `run_dir/links.md`.
        flow_run_id: Prefect flow_run id (or "local" / None when not in Prefect).
        roles: ordered role list for the formula (used for tmux hints).
        sessions: mapping role → Claude session UUID (so far). Roles not
            present render as `—`.
        tmux_session_prefix: e.g. `"po-<issue_with_dots_replaced>"`. When
            None, tmux hint section is skipped.
        extra_links: free-form `{label: url}` to append at the top
            (Logfire, Slack thread, vendor dashboard, etc.).

    Returns: path of the file written.
    """
    sessions = dict(sessions or {})
    extra_links = dict(extra_links or {})

    out: list[str] = [f"# {issue_id} — run handles\n\n"]

    url = prefect_run_url(flow_run_id)
    if url:
        out.append(f"**Prefect UI**: {url}\n")
    if flow_run_id:
        out.append(f"**Flow run id**: `{flow_run_id}`\n")
    out.append(f"**Run dir**: `{run_dir}`\n")
    for label, link in extra_links.items():
        out.append(f"**{label}**: {link}\n")
    out.append("\n")

    if tmux_session_prefix and roles:
        out.append("## Lurk (during run)\n\nAttach to a role's tmux session:\n\n")
        out.append("```bash\n")
        for role in roles:
            out.append(f"tmux attach -t {tmux_session_prefix}-{role}\n")
        out.append("```\n\n")

    if roles:
        out.append("## Resume a Claude session\n\n")
        show_jsonl = rig_path is not None
        if show_jsonl:
            out.append("| role | session_id | history |\n|---|---|---|\n")
        else:
            out.append("| role | session_id |\n|---|---|\n")
        any_sid = False
        for role in roles:
            sid = sessions.get(role)
            if sid:
                any_sid = True
                row = f"| {role} | `{sid}` |"
                if show_jsonl:
                    jsonl = claude_session_jsonl(rig_path, sid)  # type: ignore[arg-type]
                    row += f" `{jsonl}` |"
                out.append(row + "\n")
            else:
                row = f"| {role} | — |"
                if show_jsonl:
                    row += " — |"
                out.append(row + "\n")
        if not any_sid:
            out.append(
                "\n_(none yet — UUIDs land here after each role's first turn)_\n"
            )
        out.append(
            "\nResume one outside the flow:\n\n"
            "```bash\n"
            "claude --print --resume <uuid> --fork-session\n"
            "```\n\n"
            f"Or via PO: `po sessions {issue_id} --resume <role>`\n"
        )
        if show_jsonl:
            out.append(
                "\nThe `history` column points at Claude Code's local "
                "transcript JSONL — every assistant turn, tool call, and "
                "tool result the role made. Useful for post-mortem.\n"
            )

    path = run_dir / "links.md"
    path.write_text("".join(out))
    return path
