"""Unit tests for `prefect_orchestration.parsing` (bd-metadata-backed)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from prefect_orchestration.parsing import (
    prompt_for_bead_verdict,
    read_bead_verdict,
)


class _StubSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []
        self.reply = "ack"

    def prompt(self, text: str, *, fork: bool = False) -> str:
        self.calls.append((text, fork))
        return self.reply


def _bd_show_stdout(metadata: dict) -> str:
    """Mimic `bd show <id> --json` (returns a 1-element list)."""
    return json.dumps([{"id": "test-1", "metadata": metadata}])


def test_read_bead_verdict_returns_dict_value() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = _bd_show_stdout(
            {"po.triage": {"flags": ["a"], "complexity": "moderate"}}
        )
        assert read_bead_verdict("test-1", "triage") == {
            "flags": ["a"],
            "complexity": "moderate",
        }


def test_read_bead_verdict_decodes_json_string_value() -> None:
    """`--set-metadata k=v` stores stringified JSON; reader decodes it."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = _bd_show_stdout(
            {"po.triage": json.dumps({"complexity": "trivial"})}
        )
        assert read_bead_verdict("test-1", "triage") == {"complexity": "trivial"}


def test_read_bead_verdict_missing_bead_raises_filenotfound() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "Error fetching bead"
        with pytest.raises(FileNotFoundError):
            read_bead_verdict("nonexistent", "triage")


def test_read_bead_verdict_missing_key_raises_keyerror() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = _bd_show_stdout({"po.other": {}})
        with pytest.raises(KeyError, match="po.triage"):
            read_bead_verdict("test-1", "triage")


def test_read_bead_verdict_unparseable_json_raises_valueerror() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "not-json-at-all"
        with pytest.raises(ValueError):
            read_bead_verdict("test-1", "triage")


def test_prompt_for_bead_verdict_passes_prompt_and_reads_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PO_RESUME", raising=False)
    sess = _StubSession()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = _bd_show_stdout(
            {"po.step": {"verdict": "approved"}}
        )
        out = prompt_for_bead_verdict(sess, "do thing", "test-1", "step")
    assert sess.calls == [("do thing", False)]
    assert out == {"verdict": "approved"}


def test_prompt_for_bead_verdict_fork_forwards_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PO_RESUME", raising=False)
    sess = _StubSession()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = _bd_show_stdout({"po.step": {}})
        prompt_for_bead_verdict(sess, "p", "test-1", "step", fork=True)
    assert sess.calls == [("p", True)]


def test_prompt_for_bead_verdict_resume_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PO_RESUME=1 + metadata present → agent NOT prompted."""
    monkeypatch.setenv("PO_RESUME", "1")
    sess = _StubSession()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = _bd_show_stdout(
            {"po.step": {"cached": True}}
        )
        out = prompt_for_bead_verdict(sess, "p", "test-1", "step")
    assert sess.calls == []
    assert out == {"cached": True}


def test_prompt_for_bead_verdict_resume_falls_through_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PO_RESUME=1 + no metadata → agent IS prompted."""
    monkeypatch.setenv("PO_RESUME", "1")
    sess = _StubSession()
    responses = iter([
        # First call: no metadata yet
        (0, _bd_show_stdout({})),
        # After prompt: agent wrote it
        (0, _bd_show_stdout({"po.step": {"now": "here"}})),
    ])

    def fake_run(*_args, **_kwargs):
        rc, out = next(responses)
        m = type("P", (), {})()
        m.returncode = rc
        m.stdout = out
        m.stderr = ""
        return m

    with patch("subprocess.run", side_effect=fake_run):
        out = prompt_for_bead_verdict(sess, "p", "test-1", "step")
    assert sess.calls == [("p", False)]
    assert out == {"now": "here"}
