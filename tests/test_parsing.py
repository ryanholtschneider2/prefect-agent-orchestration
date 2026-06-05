"""Unit tests for `prefect_orchestration.parsing` (bd-metadata-backed)."""

from __future__ import annotations

import json
import logging
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import prefect_orchestration.parsing as parsing_module
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


def _mock_run_ok(metadata: dict) -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = _bd_show_stdout(metadata)
    m.stderr = ""
    return m


def _mock_run_fail() -> MagicMock:
    m = MagicMock()
    m.returncode = 1
    m.stdout = ""
    m.stderr = "storage is nil"
    return m


@pytest.fixture(autouse=True)
def _reset_parsing_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear verdict cache + zero retry delays so tests run fast."""
    parsing_module._verdict_cache.clear()
    monkeypatch.setattr(parsing_module, "_RETRY_DELAYS", [0, 0])


# ---------------------------------------------------------------------------
# Original tests (all must still pass with retry logic in place)
# ---------------------------------------------------------------------------


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
        mock_run.return_value.stdout = _bd_show_stdout({"po.step": {"cached": True}})
        out = prompt_for_bead_verdict(sess, "p", "test-1", "step")
    assert sess.calls == []
    assert out == {"cached": True}


def test_prompt_for_bead_verdict_resume_falls_through_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PO_RESUME=1 + no metadata → agent IS prompted."""
    monkeypatch.setenv("PO_RESUME", "1")
    sess = _StubSession()
    responses = iter(
        [
            # First call: no metadata yet
            (0, _bd_show_stdout({})),
            # After prompt: agent wrote it
            (0, _bd_show_stdout({"po.step": {"now": "here"}})),
        ]
    )

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


# ---------------------------------------------------------------------------
# New retry / cache tests
# ---------------------------------------------------------------------------


def test_read_bead_verdict_retries_on_failure() -> None:
    """bd fails twice then succeeds on the 3rd attempt."""
    responses = [
        _mock_run_fail(),
        _mock_run_fail(),
        _mock_run_ok({"po.triage": {"ok": True}}),
    ]
    with patch("subprocess.run", side_effect=responses) as mock_run:
        result = read_bead_verdict("test-1", "triage")
    assert mock_run.call_count == 3
    assert result == {"ok": True}


def test_read_bead_verdict_cache_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """All retries fail, cache hit → returns cached value and logs a warning."""
    parsing_module._verdict_cache[("test-1", "triage")] = {"from": "cache"}
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_run_fail()
        with caplog.at_level(logging.WARNING, logger="prefect_orchestration.parsing"):
            result = read_bead_verdict("test-1", "triage")
    assert result == {"from": "cache"}
    assert mock_run.call_count == 3
    assert any("cached" in r.message for r in caplog.records)


def test_read_bead_verdict_no_cache_reraises() -> None:
    """All retries fail, empty cache → raises FileNotFoundError."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_run_fail()
        with pytest.raises(FileNotFoundError):
            read_bead_verdict("test-1", "triage")
    assert mock_run.call_count == 3


def test_read_bead_verdict_timeout() -> None:
    """TimeoutExpired is retried; all 3 attempts timeout → raises."""
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired("bd", 10),
    ) as mock_run:
        with pytest.raises(subprocess.TimeoutExpired):
            read_bead_verdict("test-1", "triage")
    assert mock_run.call_count == 3


def test_read_bead_verdict_corrupt_json_retried() -> None:
    """First call returns corrupt JSON; second call returns valid JSON."""
    corrupt = MagicMock(returncode=0, stdout="not-json", stderr="")
    good = _mock_run_ok({"po.triage": {"ok": True}})
    with patch("subprocess.run", side_effect=[corrupt, good]) as mock_run:
        result = read_bead_verdict("test-1", "triage")
    assert mock_run.call_count == 2
    assert result == {"ok": True}


def test_read_bead_verdict_missing_key_not_retried() -> None:
    """KeyError (missing metadata key) propagates immediately — no retry."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_run_ok({"po.other": {}})
        with pytest.raises(KeyError, match="po.triage"):
            read_bead_verdict("test-1", "triage")
    # Only 1 subprocess call — KeyError is not a transient bd failure.
    assert mock_run.call_count == 1


def test_read_bead_verdict_populates_cache_on_success() -> None:
    """A successful read populates the cache for future fallback use."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_run_ok({"po.triage": {"x": 1}})
        read_bead_verdict("test-1", "triage")
    assert parsing_module._verdict_cache[("test-1", "triage")] == {"x": 1}


# ---------------------------------------------------------------------------
# br backend read path (delegated via beads_backend.read_verdict)
# ---------------------------------------------------------------------------


def _br_show_stdout(comments: list[dict]) -> str:
    """Mimic `br show <id> --json` (1-element list with a comments array)."""
    return json.dumps([{"id": "test-1", "title": "t", "comments": comments}])


def test_read_bead_verdict_br_latest_comment_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parsing_module, "resolve_backend", lambda _rig: "br")
    comments = [
        {"id": 1, "text": 'po-verdict:triage:{"complexity": "trivial"}'},
        {"id": 2, "text": 'po-verdict:triage:{"complexity": "moderate"}'},
    ]
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = _br_show_stdout(comments)
        mock_run.return_value.stderr = ""
        assert read_bead_verdict("test-1", "triage") == {"complexity": "moderate"}


def test_read_bead_verdict_br_missing_comment_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parsing_module, "resolve_backend", lambda _rig: "br")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = _br_show_stdout(
            [{"id": 1, "text": "po-verdict:linter:{}"}]
        )
        mock_run.return_value.stderr = ""
        with pytest.raises(KeyError):
            read_bead_verdict("test-1", "triage")
    assert mock_run.call_count == 1  # KeyError is a semantic failure: no retry
