"""E2E test for po_formulas.mail against a real `bd` binary.

Initializes a throwaway beads repo in a tmp dir, then exercises the
full send → inbox → mark_read cycle without mocking subprocess.run.
Skipped automatically if `bd` is not installed.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from po_formulas import mail as mail_mod


pytestmark = pytest.mark.skipif(
    shutil.which("bd") is None, reason="bd not on PATH; skipping real-bd e2e"
)


@pytest.fixture
def beads_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Initialize a fresh beads repo in tmp_path and chdir into it for the test."""
    subprocess.run(
        ["bd", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    monkeypatch.chdir(tmp_path)
    yield tmp_path


def test_send_creates_real_bd_issue(beads_repo: Path) -> None:
    mail_id = mail_mod.send("builder", "fix X", "see plan.md", from_agent="critic")
    assert mail_id
    # Verify with a raw `bd show` that the issue exists and has expected shape.
    show = subprocess.run(
        ["bd", "show", mail_id, "--json"],
        cwd=beads_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "[mail:builder] fix X" in show.stdout
    assert "mail-to:builder" in show.stdout


def test_inbox_returns_sent_mail(beads_repo: Path) -> None:
    sent_id = mail_mod.send("builder", "fix X", "see plan.md", from_agent="critic")

    mails = mail_mod.inbox("builder")
    assert len(mails) == 1, f"expected 1 mail, got {len(mails)}: {mails}"
    m = mails[0]
    assert m.id == sent_id
    assert m.to == "builder"
    assert m.subject == "fix X"
    assert "see plan.md" in m.body
    assert m.from_agent == "critic"


def test_mark_read_removes_from_inbox(beads_repo: Path) -> None:
    sent_id = mail_mod.send("builder", "done", "ack", from_agent="critic")
    assert mail_mod.inbox("builder"), "mail should be in inbox before mark_read"

    mail_mod.mark_read(sent_id)

    remaining = mail_mod.inbox("builder")
    assert remaining == [], f"mail should be closed after mark_read, got: {remaining}"


def test_inbox_isolates_recipients(beads_repo: Path) -> None:
    mail_mod.send("builder", "for builder", "b1", from_agent="critic")
    mail_mod.send("critic", "for critic", "c1", from_agent="builder")

    builder_box = mail_mod.inbox("builder")
    critic_box = mail_mod.inbox("critic")

    assert len(builder_box) == 1
    assert builder_box[0].subject == "for builder"
    assert len(critic_box) == 1
    assert critic_box[0].subject == "for critic"


def test_critic_to_builder_handoff_demo(beads_repo: Path) -> None:
    """AC #3 end-to-end against real bd.

    Critic sends "fix X" to builder. On the next turn, builder reads the
    inbox and sees the message.
    """
    critic_sent = mail_mod.send("builder", "fix X", "see line 42", from_agent="critic")

    # Fresh "turn" — builder looks at inbox.
    builder_inbox = mail_mod.inbox("builder")
    assert len(builder_inbox) == 1
    m = builder_inbox[0]
    assert m.id == critic_sent
    assert m.subject == "fix X"
    assert m.from_agent == "critic"
    assert "see line 42" in m.body
