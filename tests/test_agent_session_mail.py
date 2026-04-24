"""Unit tests for AgentSession unread-mail auto-injection.

Covers prefect-orchestration-4ja.2 acceptance criteria:
1. unread mail rendered as <mail-inbox> block
2. messages marked read on success
3. messages stay unread on failure
4. empty inbox => no block
5. skip_mail_inject bypass
6. concurrent mid-turn mail not auto-marked
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from prefect_orchestration.agent_session import AgentSession


@dataclass
class RecordingBackend:
    """Captures the prompt argument; optionally raises."""

    captured: list[str] = field(default_factory=list)
    raise_exc: Exception | None = None
    return_sid: str = "sid-after"

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None,
        cwd: Path,
        fork: bool = False,
        model: str = "opus",
    ) -> tuple[str, str]:
        self.captured.append(prompt)
        if self.raise_exc is not None:
            raise self.raise_exc
        return "ok", self.return_sid


def _mk_mail(mid: str, subject: str, body: str, from_agent: str = "critic"):
    return SimpleNamespace(
        id=mid,
        subject=subject,
        body=body,
        from_agent=from_agent,
        created_at=datetime(2026, 4, 24, 12, 3),
    )


def test_empty_inbox_no_block_prepended(tmp_path: Path) -> None:
    backend = RecordingBackend()
    marked: list[str] = []
    sess = AgentSession(
        role="builder",
        repo_path=tmp_path,
        backend=backend,
        mail_fetcher=lambda role: [],
        mail_marker=marked.append,
    )
    out = sess.prompt("hello world")
    assert out == "ok"
    assert backend.captured == ["hello world"]
    assert "<mail-inbox>" not in backend.captured[0]
    assert marked == []


def test_nonempty_inbox_renders_block_and_preserves_prompt(tmp_path: Path) -> None:
    mails = [
        _mk_mail("m1", "fix build step", "build is red"),
        _mk_mail("m2", "AC5 unmet", "test missing"),
    ]
    backend = RecordingBackend()
    sess = AgentSession(
        role="builder",
        repo_path=tmp_path,
        backend=backend,
        mail_fetcher=lambda role: mails,
        mail_marker=lambda mid: None,
    )
    sess.prompt("ORIGINAL_PROMPT")
    sent = backend.captured[0]
    assert sent.startswith("<mail-inbox>\n")
    assert "subject: fix build step" in sent
    assert "build is red" in sent
    assert "subject: AC5 unmet" in sent
    assert "test missing" in sent
    assert "</mail-inbox>" in sent
    # original prompt preserved AFTER the block
    block_end = sent.index("</mail-inbox>") + len("</mail-inbox>")
    assert "ORIGINAL_PROMPT" in sent[block_end:]
    # exactly one separator '---' between two mails (not a stray dead one)
    assert sent.count("\n---\n") == 1


def test_successful_turn_marks_messages_read(tmp_path: Path) -> None:
    mails = [_mk_mail("m1", "s1", "b1"), _mk_mail("m2", "s2", "b2")]
    backend = RecordingBackend()
    marked: list[str] = []
    sess = AgentSession(
        role="critic",
        repo_path=tmp_path,
        backend=backend,
        mail_fetcher=lambda role: mails,
        mail_marker=marked.append,
    )
    sess.prompt("p")
    assert marked == ["m1", "m2"]
    assert sess.session_id == "sid-after"


def test_failed_turn_leaves_messages_unread(tmp_path: Path) -> None:
    mails = [_mk_mail("m1", "s1", "b1")]
    backend = RecordingBackend(raise_exc=RuntimeError("boom"))
    marked: list[str] = []
    sess = AgentSession(
        role="builder",
        repo_path=tmp_path,
        backend=backend,
        mail_fetcher=lambda role: mails,
        mail_marker=marked.append,
    )
    with pytest.raises(RuntimeError, match="boom"):
        sess.prompt("p")
    assert marked == []
    # session_id not updated on failure
    assert sess.session_id is None


def test_skip_mail_inject_bypasses_fetcher(tmp_path: Path) -> None:
    calls: list[str] = []

    def fetch(role: str):
        calls.append(role)
        return [_mk_mail("m1", "s1", "b1")]

    backend = RecordingBackend()
    sess = AgentSession(
        role="builder",
        repo_path=tmp_path,
        backend=backend,
        mail_fetcher=fetch,
        mail_marker=lambda mid: None,
        skip_mail_inject=True,
    )
    sess.prompt("p")
    assert calls == []
    assert backend.captured == ["p"]


def test_no_fetcher_means_no_block(tmp_path: Path) -> None:
    backend = RecordingBackend()
    sess = AgentSession(role="builder", repo_path=tmp_path, backend=backend)
    sess.prompt("p")
    assert backend.captured == ["p"]


def test_concurrent_mail_not_auto_marked(tmp_path: Path) -> None:
    """Mail arriving mid-turn (i.e. between fetch and mark) is not closed."""
    state = {
        "snapshot": [_mk_mail("m1", "s1", "b1")],
        "after": [_mk_mail("m1", "s1", "b1"), _mk_mail("m2", "late", "arrived")],
    }
    fetch_calls = {"n": 0}

    def fetch(role: str):
        fetch_calls["n"] += 1
        return state["snapshot"]

    backend = RecordingBackend()
    marked: list[str] = []
    sess = AgentSession(
        role="builder",
        repo_path=tmp_path,
        backend=backend,
        mail_fetcher=fetch,
        mail_marker=marked.append,
    )
    sess.prompt("p")
    # Only the snapshot's IDs are marked; m2 arrived after fetch.
    assert marked == ["m1"]
    assert fetch_calls["n"] == 1


def test_fetcher_exception_does_not_break_turn(tmp_path: Path) -> None:
    def fetch(role: str):
        raise RuntimeError("bd unavailable")

    backend = RecordingBackend()
    sess = AgentSession(
        role="builder",
        repo_path=tmp_path,
        backend=backend,
        mail_fetcher=fetch,
        mail_marker=lambda mid: None,
    )
    out = sess.prompt("p")
    assert out == "ok"
    assert backend.captured == ["p"]


def test_inbox_capped_at_max(tmp_path: Path) -> None:
    from prefect_orchestration.agent_session import MAX_INBOX_MESSAGES

    mails = [
        SimpleNamespace(
            id=f"m{i}",
            subject=f"s{i}",
            body=f"b{i}",
            from_agent="x",
            created_at=datetime(2026, 4, 24, 12, i % 60),
        )
        for i in range(MAX_INBOX_MESSAGES + 5)
    ]
    backend = RecordingBackend()
    marked: list[str] = []
    sess = AgentSession(
        role="builder",
        repo_path=tmp_path,
        backend=backend,
        mail_fetcher=lambda role: mails,
        mail_marker=marked.append,
    )
    sess.prompt("p")
    assert len(marked) == MAX_INBOX_MESSAGES
