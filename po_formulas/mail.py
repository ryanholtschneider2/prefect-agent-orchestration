"""Beads-as-mail: lightweight agent-to-agent messaging over `bd`.

Design (see planning/prefect-orchestration-5kj/plan.md):

- Each message is a `bd` issue with `--type=task`, labels `mail` and
  `mail-to:<recipient>`, `--assignee=<recipient>`, priority 4 (backlog
  so it does not surface in `bd ready`), and title `[mail:<to>] <subject>`.
- `send()` creates the issue; `inbox()` lists open mail for an agent;
  `mark_read()` closes the issue with `--reason=read`.
- No `bd` on PATH → `send()` raises `RuntimeError`, `inbox()` returns `[]`,
  `mark_read()` is a no-op. Same style as
  `prefect_orchestration.beads_meta`.

Message semantics are intentionally fire-and-forget. If richer thread/ack
semantics are needed, escalate to `mcp-agent-mail`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime

MAIL_LABEL = "mail"
_TITLE_PREFIX = "[mail:"


def _bd_available() -> bool:
    return shutil.which("bd") is not None


@dataclass(frozen=True)
class Mail:
    """A single piece of mail pulled from the beads tracker."""

    id: str
    to: str
    from_agent: str | None
    subject: str
    body: str
    created_at: datetime | None = None


def send(
    to: str,
    subject: str,
    body: str,
    *,
    from_agent: str | None = None,
) -> str:
    """Create a mail issue addressed to `to`. Returns the new issue ID.

    Raises `RuntimeError` if `bd` is not on PATH — mail without beads is a
    silent failure mode we refuse to offer.
    """
    if not _bd_available():
        raise RuntimeError("bd is not on PATH; cannot send mail")

    title = f"{_TITLE_PREFIX}{to}] {subject}"
    description = body
    if from_agent:
        description = f"{body}\n\n---\nFrom: {from_agent}\n"

    cmd = [
        "bd",
        "create",
        "--title",
        title,
        "--description",
        description,
        "--type",
        "task",
        "--priority",
        "4",
        "--assignee",
        to,
        "--labels",
        f"{MAIL_LABEL},mail-to:{to}",
        "--json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return _parse_created_id(proc.stdout)


def inbox(agent: str, *, include_read: bool = False) -> list[Mail]:
    """Return mail addressed to `agent`. Closed (read) mail excluded by default."""
    if not _bd_available():
        return []

    cmd = [
        "bd",
        "list",
        "--labels",
        MAIL_LABEL,
        "--assignee",
        agent,
        "--json",
    ]
    if not include_read:
        cmd += ["--status", "open"]

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        return []

    try:
        rows = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(rows, list):
        return []

    mails: list[Mail] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = row.get("title") or ""
        to, subject = _parse_title(title, fallback_to=agent)
        body, from_agent = _split_body(row.get("description") or "")
        mails.append(
            Mail(
                id=str(row.get("id", "")),
                to=to,
                from_agent=from_agent,
                subject=subject,
                body=body,
                created_at=_parse_ts(row.get("created_at") or row.get("created")),
            )
        )
    return mails


def mark_read(mail_id: str) -> None:
    """Close the mail issue. No-op when bd is absent."""
    if not _bd_available():
        return
    subprocess.run(
        ["bd", "close", mail_id, "--reason", "read"],
        check=False,
    )


def _parse_created_id(stdout: str) -> str:
    """Extract issue ID from `bd create --json` output. Tolerant of shape."""
    stdout = stdout.strip()
    if not stdout:
        raise RuntimeError("bd create returned empty stdout")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        # Fallback: bd sometimes prints "Created issue <id>: ..." on stderr/stdout.
        for token in stdout.split():
            if token.startswith("prefect-orchestration-") or token.startswith("bd-"):
                return token.rstrip(":")
        raise RuntimeError(f"could not parse bd create output: {stdout!r}") from None

    if isinstance(payload, dict):
        issue_id = payload.get("id") or payload.get("issue_id")
        if issue_id:
            return str(issue_id)
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict) and first.get("id"):
            return str(first["id"])
    raise RuntimeError(f"no id in bd create output: {stdout!r}")


def _parse_title(title: str, *, fallback_to: str) -> tuple[str, str]:
    """`[mail:builder] fix X` -> ('builder', 'fix X'). Non-mail titles pass through."""
    if not title.startswith(_TITLE_PREFIX):
        return fallback_to, title
    rest = title[len(_TITLE_PREFIX) :]
    end = rest.find("]")
    if end == -1:
        return fallback_to, title
    to = rest[:end]
    subject = rest[end + 1 :].lstrip()
    return to, subject


def _split_body(description: str) -> tuple[str, str | None]:
    """Split the description into (body, from_agent) using the footer convention."""
    marker = "\n---\nFrom: "
    idx = description.rfind(marker)
    if idx == -1:
        return description, None
    body = description[:idx].rstrip()
    footer = description[idx + len(marker) :].strip()
    from_agent = footer.splitlines()[0].strip() if footer else None
    return body, from_agent or None


def _parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
