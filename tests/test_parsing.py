"""Unit tests for `prefect_orchestration.parsing`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prefect_orchestration.parsing import (
    prompt_for_verdict,
    read_verdict,
    verdicts_dir,
)


class _StubSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []
        self.reply = "ack"

    def prompt(self, text: str, *, fork_session: bool = False) -> str:
        self.calls.append((text, fork_session))
        return self.reply


def test_verdicts_dir_creates(tmp_path: Path) -> None:
    d = verdicts_dir(tmp_path)
    assert d == tmp_path / "verdicts"
    assert d.is_dir()


def test_read_verdict_returns_parsed_json(tmp_path: Path) -> None:
    (verdicts_dir(tmp_path) / "triage.json").write_text('{"flags": ["a"]}')
    assert read_verdict(tmp_path, "triage") == {"flags": ["a"]}


def test_read_verdict_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_verdict(tmp_path, "missing")


def test_read_verdict_invalid_json_raises(tmp_path: Path) -> None:
    (verdicts_dir(tmp_path) / "x.json").write_text("not json")
    with pytest.raises(ValueError):
        read_verdict(tmp_path, "x")


def test_prompt_for_verdict_passes_prompt_and_returns_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PO_RESUME", raising=False)
    (verdicts_dir(tmp_path) / "step.json").write_text(
        json.dumps({"verdict": "approved"})
    )
    sess = _StubSession()
    out = prompt_for_verdict(sess, "do thing", tmp_path, "step")
    assert sess.calls == [("do thing", False)]
    assert out == {"verdict": "approved"}


def test_prompt_for_verdict_fork_forwards_kwarg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PO_RESUME", raising=False)
    (verdicts_dir(tmp_path) / "step.json").write_text("{}")
    sess = _StubSession()
    prompt_for_verdict(sess, "p", tmp_path, "step", fork=True)
    assert sess.calls == [("p", True)]


def test_prompt_for_verdict_missing_verdict_raises(tmp_path: Path) -> None:
    sess = _StubSession()
    with pytest.raises(FileNotFoundError):
        prompt_for_verdict(sess, "p", tmp_path, "never-written")
