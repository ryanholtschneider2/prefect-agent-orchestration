"""Unit tests for po_formulas.mail — subprocess mocked out."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from po_formulas import mail
from po_formulas.mail import Mail, inbox, mark_read, send


class _FakeProc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class FakeBdBackend:
    """In-memory bd: records create calls, replays on list, marks closed on close."""

    def __init__(self) -> None:
        self.issues: list[dict] = []
        self._next = 1
        self.calls: list[list[str]] = []

    def run(self, cmd: list[str], **_kwargs: object) -> _FakeProc:
        self.calls.append(cmd)
        if cmd[:2] == ["bd", "create"]:
            return self._create(cmd)
        if cmd[:2] == ["bd", "list"]:
            return self._list(cmd)
        if cmd[:2] == ["bd", "close"]:
            return self._close(cmd)
        return _FakeProc(returncode=1)

    def _flag(self, cmd: list[str], name: str) -> str | None:
        if name in cmd:
            i = cmd.index(name)
            if i + 1 < len(cmd):
                return cmd[i + 1]
        return None

    def _flag_all(self, cmd: list[str], name: str) -> list[str]:
        vals: list[str] = []
        for i, tok in enumerate(cmd):
            if tok == name and i + 1 < len(cmd):
                vals.append(cmd[i + 1])
        return vals

    def _create(self, cmd: list[str]) -> _FakeProc:
        issue = {
            "id": f"mock-{self._next}",
            "title": self._flag(cmd, "--title") or "",
            "description": self._flag(cmd, "--description") or "",
            "assignee": self._flag(cmd, "--assignee") or "",
            "labels": (self._flag(cmd, "--labels") or "").split(","),
            "status": "open",
            "priority": self._flag(cmd, "--priority"),
            "type": self._flag(cmd, "--type"),
        }
        self._next += 1
        self.issues.append(issue)
        return _FakeProc(stdout=json.dumps({"id": issue["id"]}))

    def _list(self, cmd: list[str]) -> _FakeProc:
        assignee = self._flag(cmd, "--assignee")
        # Real `bd list` uses singular `--label` (repeatable, AND-match).
        required_labels = self._flag_all(cmd, "--label")
        status = self._flag(cmd, "--status")
        out = []
        for issue in self.issues:
            if assignee and issue["assignee"] != assignee:
                continue
            if required_labels and not all(
                lbl in issue["labels"] for lbl in required_labels
            ):
                continue
            if status and issue["status"] != status:
                continue
            out.append(issue)
        return _FakeProc(stdout=json.dumps(out))

    def _close(self, cmd: list[str]) -> _FakeProc:
        issue_id = cmd[2]
        for issue in self.issues:
            if issue["id"] == issue_id:
                issue["status"] = "closed"
        return _FakeProc(stdout="")


@pytest.fixture
def fake_bd(monkeypatch: pytest.MonkeyPatch) -> FakeBdBackend:
    backend = FakeBdBackend()
    monkeypatch.setattr(mail.shutil, "which", lambda _cmd: "/usr/bin/bd")
    monkeypatch.setattr(mail.subprocess, "run", backend.run)
    return backend


def test_send_invokes_bd_create_with_mail_shape(fake_bd: FakeBdBackend) -> None:
    msg_id = send("builder", "fix X", "see plan.md line 12", from_agent="critic")

    assert msg_id == "mock-1"
    assert len(fake_bd.calls) == 1
    cmd = fake_bd.calls[0]
    assert cmd[:2] == ["bd", "create"]
    assert "--type" in cmd and cmd[cmd.index("--type") + 1] == "task"
    assert "--assignee" in cmd and cmd[cmd.index("--assignee") + 1] == "builder"
    assert cmd[cmd.index("--priority") + 1] == "4"
    labels = cmd[cmd.index("--labels") + 1]
    assert "mail" in labels.split(",")
    assert "mail-to:builder" in labels.split(",")
    title = cmd[cmd.index("--title") + 1]
    assert title == "[mail:builder] fix X"
    desc = cmd[cmd.index("--description") + 1]
    assert "see plan.md line 12" in desc
    assert "From: critic" in desc


def test_inbox_uses_singular_label_flag_and_unlimited(fake_bd: FakeBdBackend) -> None:
    """Regression: `bd list` takes `--label` (singular), not `--labels`.

    `bd create` uses plural `--labels`; `bd list` uses singular `--label`
    (repeatable). Getting this wrong makes inbox() silently return nothing
    against a real `bd`. Also asserts `--limit 0` so large inboxes do not
    truncate at the default cap of 50.
    """
    mail_mod_inbox = inbox("builder")
    assert mail_mod_inbox == []

    assert len(fake_bd.calls) == 1
    cmd = fake_bd.calls[0]
    assert cmd[:2] == ["bd", "list"]
    # Must use singular --label, and must NOT use plural --labels.
    assert "--label" in cmd
    assert "--labels" not in cmd
    # Filter by both base label and recipient-scoped label.
    label_positions = [i for i, t in enumerate(cmd) if t == "--label"]
    label_values = [cmd[i + 1] for i in label_positions]
    assert "mail" in label_values
    assert "mail-to:builder" in label_values
    # Unlimited result set.
    assert "--limit" in cmd and cmd[cmd.index("--limit") + 1] == "0"


def test_inbox_parses_bd_list_output(fake_bd: FakeBdBackend) -> None:
    send("builder", "fix X", "see plan.md", from_agent="critic")
    send("verifier", "approve?", "iter 2 ready", from_agent="builder")

    mails = inbox("builder")

    assert len(mails) == 1
    m = mails[0]
    assert isinstance(m, Mail)
    assert m.to == "builder"
    assert m.subject == "fix X"
    assert m.body == "see plan.md"
    assert m.from_agent == "critic"


def test_critic_messages_builder_demo(fake_bd: FakeBdBackend) -> None:
    """AC #3: critic sends 'fix X', builder reads it on the next turn."""

    # Turn 1: critic side
    msg_id = send("builder", "fix X", "bad handling in mail.py", from_agent="critic")

    # Turn 2: builder side — inbox check
    pending = inbox("builder")
    assert [m.subject for m in pending] == ["fix X"]
    assert pending[0].id == msg_id
    assert pending[0].from_agent == "critic"

    # Builder acknowledges
    mark_read(pending[0].id)
    assert inbox("builder") == []


def test_mark_read_closes_issue(fake_bd: FakeBdBackend) -> None:
    msg_id = send("builder", "hi", "ping")
    mark_read(msg_id)
    closed = [i for i in fake_bd.issues if i["id"] == msg_id][0]
    assert closed["status"] == "closed"


def test_send_raises_when_bd_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mail.shutil, "which", lambda _cmd: None)
    with pytest.raises(RuntimeError, match="bd is not on PATH"):
        send("builder", "hi", "body")


def test_inbox_empty_when_bd_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mail.shutil, "which", lambda _cmd: None)
    assert inbox("builder") == []


def test_inbox_tolerates_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mail.shutil, "which", lambda _cmd: "/usr/bin/bd")
    monkeypatch.setattr(
        mail.subprocess,
        "run",
        lambda *_a, **_kw: _FakeProc(stdout="not-json"),
    )
    assert inbox("builder") == []


def test_prompt_fragment_exists_and_mentions_inbox() -> None:
    # Mail prompt fragment lives in the pack at `prompts/mail.md`
    # (commit 3f39b98 moved it out of core; later renamed under prompts/).
    path = Path(mail.__file__).resolve().parent / "prompts" / "mail.md"
    if not path.exists():  # pragma: no cover - older pack versions
        pytest.skip(f"mail prompt fragment not present at {path}")
    text = path.read_text()
    assert "inbox" in text.lower()
    assert "mark_read" in text
    assert "{{role}}" in text


def test_inbox_excludes_closed_by_default(fake_bd: FakeBdBackend) -> None:
    msg_id = send("builder", "ping", "one")
    send("builder", "pong", "two")
    mark_read(msg_id)

    mails = inbox("builder")
    subjects = [m.subject for m in mails]
    assert subjects == ["pong"]


def test_parse_title_handles_non_mail_titles() -> None:
    to, subject = mail._parse_title("plain task title", fallback_to="me")
    assert to == "me"
    assert subject == "plain task title"


def test_split_body_without_footer() -> None:
    body, from_agent = mail._split_body("just a body, no footer")
    assert body == "just a body, no footer"
    assert from_agent is None
