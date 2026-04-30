"""Publish per-role file outputs as Prefect artifacts.

Every role in a formula run drops its working notes onto disk
under `<run_dir>/...` (markdown reports, lint logs, build diffs, etc.).
The Prefect UI only knows about whatever the flow code explicitly
publishes via `create_markdown_artifact` / `create_link_artifact`. This
module is the single hook tasks use to push those file bodies into the
Prefect run page so reviewers don't need shell access to the rig.

Two artifact kinds per role-iteration:

* **markdown body** — full file content for each canonical output file
  the role wrote. `.diff` and `.log` files are wrapped in a fenced code
  block so the UI renders them readably. Bodies > `MAX_BODY_BYTES` are
  truncated with a footer pointing at the source path.
* **transcript link** — a `file://` link artifact pointing at a symlink
  (under `<run_dir>/transcripts/`) into the role's Claude Code session
  JSONL. The symlink keeps the artifact href stable even after the
  session UUID rolls forward on subsequent turns; the JSONL itself
  always lives in `~/.claude/projects/<slug>/<uuid>.jsonl`.

Keys are slug-safe (`^[a-z0-9-]+$`, ≤ 256 chars) per Prefect's artifact
key constraints. Helper is best-effort — file-not-found / Prefect-API
hiccups never fail the underlying task.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from prefect.artifacts import create_link_artifact, create_markdown_artifact

from prefect_orchestration.run_handles import claude_session_jsonl

logger = logging.getLogger(__name__)

# Prefect rejects artifact keys outside `^[a-z0-9-]+$`; cap at 256 chars.
_KEY_RE = re.compile(r"[^a-z0-9-]+")
_MAX_KEY_LEN = 256

# Cap each markdown artifact at ~1 MB. Prefect's UI/DB don't enforce a
# hard cap but oversized blobs make the run page sluggish. Truncated
# bodies always include a pointer to the on-disk source.
MAX_BODY_BYTES = 1_000_000

# Map file suffix → fenced-code language hint for nicer UI rendering.
_FENCE_LANG: dict[str, str] = {
    ".diff": "diff",
    ".patch": "diff",
    ".log": "text",
    ".txt": "text",
    ".json": "json",
}


def slugify_key(*parts: str) -> str:
    """Build a Prefect-safe artifact key from arbitrary parts.

    Lowercases, replaces non-`[a-z0-9-]` runs with `-`, trims dashes,
    and truncates to ≤ 256 chars. Empty result falls back to `artifact`
    so we never call the API with an invalid key.
    """
    raw = "-".join(p for p in parts if p)
    cleaned = _KEY_RE.sub("-", raw.lower()).strip("-")
    if not cleaned:
        cleaned = "artifact"
    return cleaned[:_MAX_KEY_LEN].rstrip("-") or "artifact"


def _format_body(path: Path) -> str:
    """Read `path` and return a markdown body for `create_markdown_artifact`.

    Wraps `.diff` / `.log` / `.json` in a fenced block; markdown is
    inlined as-is. Truncates oversized files at `MAX_BODY_BYTES` with a
    footer pointing at the on-disk source for reviewers who need the
    full content.
    """
    try:
        raw = path.read_text(errors="replace")
    except OSError as exc:
        return f"_(failed to read `{path}`: {exc})_"

    truncated = False
    if len(raw.encode("utf-8", errors="replace")) > MAX_BODY_BYTES:
        # Truncate by characters as a coarse approximation; keep the
        # tail (most recent log lines / final diff hunks) since that's
        # usually where the signal lives.
        raw = raw[-MAX_BODY_BYTES:]
        truncated = True

    suffix = path.suffix.lower()
    lang = _FENCE_LANG.get(suffix)
    if lang is not None:
        body = f"```{lang}\n{raw}\n```"
    else:
        body = raw

    if truncated:
        body += f"\n\n_[truncated, see `{path}` for full content]_"
    return body


def _publish_transcript_link(
    run_dir: Path,
    rig_path: Path,
    role: str,
    iter_n: int,
    session_id: str | None,
    artifact_key: str,
) -> None:
    """Symlink the role's Claude JSONL into `run_dir/transcripts/` and link it.

    Skips silently when no session UUID has been recorded yet (first
    turn hasn't written one) or when the source JSONL hasn't appeared
    on disk yet (Claude flushes asynchronously). Caller treats this as
    fire-and-forget — never raises.
    """
    if not session_id:
        logger.debug("no session_id for role=%s iter=%s; skip transcript link", role, iter_n)
        return

    src = claude_session_jsonl(rig_path, session_id)
    if not src.exists():
        logger.debug("transcript jsonl not yet on disk: %s", src)
        return

    transcripts_dir = run_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    link_path = transcripts_dir / f"{role}-iter-{iter_n}.jsonl"

    # Re-runs may overwrite the same iter; replace the existing symlink
    # rather than failing. Use unlink-then-symlink to avoid a TOCTOU
    # window where two roles race to point at different sources.
    try:
        if link_path.is_symlink() or link_path.exists():
            link_path.unlink()
        link_path.symlink_to(src, target_is_directory=False)
    except FileExistsError:
        # Another worker won the race; whichever symlink landed is fine.
        pass
    except OSError as exc:
        logger.debug("failed to symlink transcript %s -> %s: %s", link_path, src, exc)
        return

    try:
        create_link_artifact(
            key=artifact_key,
            link=f"file://{link_path}",
            description=f"Claude transcript for {role} (iter {iter_n})",
        )
    except Exception as exc:  # noqa: BLE001 — Prefect-API failures must never abort a task
        logger.debug("create_link_artifact failed for %s: %s", artifact_key, exc)


def _publish_handles_artifact(
    *,
    run_dir: Path,
    rig_path: Path,
    role: str,
    iter_n: int,
    session_id: str | None,
    issue_id: str | None,
    artifact_key: str,
    tmux_scope: str | None = None,
) -> None:
    """One-card-per-task handles: tmux attach hint, Claude sid, JSONL path,
    Logfire URL slot. Lets a reviewer click straight into the role's
    live (or post-mortem) state from the Prefect run page."""
    import os

    safe_issue = (issue_id or "?").replace(".", "_")
    rows: list[str] = []
    if tmux_scope:
        # Shared-scope layout: one tmux session, one window per (issue,role).
        rows.append(
            f"**tmux**: `tmux attach -t {tmux_scope} \\; "
            f"select-window -t {issue_id or '?'}-{role}`"
        )
    else:
        tmux_session = f"po-{safe_issue}-{role.replace('.', '_')}"
        rows.append(f"**tmux**: `tmux attach -t {tmux_session}`")
    if session_id:
        rows.append(f"**Claude session_id**: `{session_id}`")
        jsonl = claude_session_jsonl(rig_path, session_id)
        rows.append(f"**Transcript JSONL**: `{jsonl}`")
        rows.append(f"**Resume**: `claude --print --resume {session_id} --fork-session`")
    else:
        rows.append("**Claude session_id**: _(not yet recorded)_")
    logfire = os.environ.get("LOGFIRE_TRACE_URL") or os.environ.get("LOGFIRE_URL")
    if logfire:
        rows.append(f"**Logfire**: {logfire}")
    rows.append(f"**Run dir**: `{run_dir}`")
    body = "\n\n".join(rows)
    try:
        create_markdown_artifact(
            key=artifact_key,
            markdown=body,
            description=f"{role} (iter {iter_n}) — run handles",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("create_markdown_artifact (handles) failed for %s: %s", artifact_key, exc)


def publish_role_artifacts(
    run_dir: Path,
    rig_path: Path,
    role: str,
    iter_n: int,
    session_id: str | None,
    output_files: list[str],
    *,
    issue_id: str | None = None,
    tmux_scope: str | None = None,
) -> None:
    """Publish file bodies + transcript link for a role's just-finished turn.

    Args:
        run_dir: per-run artifact dir (`<rig>/.planning/<formula>/<issue>/`).
        rig_path: rig root — used to locate the Claude session JSONL.
        role: role name as used in keys (e.g. `"plan-critic"`, `"build"`).
            Verdict-specific keys (e.g. `triage`, `review-iter-N`) are
            owned by the calling task; this helper picks distinct keys
            so it never collides with the existing verdict artifacts.
        iter_n: iteration counter for this role-step. Use `1` for
            singleton steps that don't loop (triage, baseline, docs, ...).
        session_id: current Claude session UUID for the role; `None`
            silently skips the transcript link.
        output_files: list of canonical output file basenames the role
            wrote into `run_dir` (e.g. `["plan.md"]`,
            `["build-iter-2.diff", "decision-log.md"]`). Missing files
            are skipped with a debug log — the helper never fails.
        issue_id: optional issue id used as a key prefix to keep keys
            unique across issues sharing a Prefect server.
    """
    issue_slug = slugify_key(issue_id) if issue_id else ""

    for fname in output_files:
        fpath = run_dir / fname
        if not fpath.exists():
            logger.debug("expected output missing for role=%s: %s", role, fpath)
            continue
        # File-stem-based keys keep the per-file artifacts distinct
        # (e.g. lint-iter-1.log vs build-iter-1.diff) and avoid
        # collisions with the verdict artifacts emitted by the task
        # itself (those use `<role>-iter-N` plain).
        body_key = slugify_key(issue_slug, "file", fpath.stem)
        body = _format_body(fpath)
        description = f"{role} output: {fname}"
        try:
            create_markdown_artifact(
                key=body_key,
                markdown=body,
                description=description,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("create_markdown_artifact failed for %s: %s", body_key, exc)

    transcript_key = slugify_key(issue_slug, "transcript", role, "iter", str(iter_n))
    _publish_transcript_link(
        run_dir=run_dir,
        rig_path=rig_path,
        role=role,
        iter_n=iter_n,
        session_id=session_id,
        artifact_key=transcript_key,
    )

    handles_key = slugify_key(issue_slug, "handles", role, "iter", str(iter_n))
    _publish_handles_artifact(
        run_dir=run_dir,
        rig_path=rig_path,
        role=role,
        iter_n=iter_n,
        session_id=session_id,
        issue_id=issue_id,
        artifact_key=handles_key,
        tmux_scope=tmux_scope,
    )
