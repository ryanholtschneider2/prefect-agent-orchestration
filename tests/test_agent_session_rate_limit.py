"""Rate-limit detection + propagation (sav.2).

Verifies:

* `_detect_rate_limit_in_pane` recognises the user-facing dialog and
  parses the reset-time substring.
* `_detect_rate_limit_in_jsonl` recognises the synthetic
  `error: "rate_limit"` assistant event Claude itself writes (the
  deterministic ground-truth signal).
* `AgentSession.prompt()` lets `RateLimitError` raised by a backend
  propagate to the caller WITHOUT firing the verdict-nudge retry —
  the turn never ran, so there's nothing to nudge.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from prefect_orchestration.agent_session import (
    AgentSession,
    RateLimitError,
    _detect_rate_limit_in_jsonl,
    _detect_rate_limit_in_pane,
)


SYNTHETIC_TEXT = "You've hit your limit · resets 1:30am (America/New_York)"


# --- pane detector ---------------------------------------------------


def test_pane_detects_classic_dialog() -> None:
    assert _detect_rate_limit_in_pane(SYNTHETIC_TEXT) == "1:30am (America/New_York)"


def test_pane_detects_when_no_resets_substring() -> None:
    # Marker present but reset time absent — still a rate limit, just less
    # informative. Empty string is the documented "found-but-no-time" return.
    assert _detect_rate_limit_in_pane("You've hit your limit and that's that") == ""


def test_pane_returns_none_when_clean() -> None:
    assert _detect_rate_limit_in_pane("normal pane output, agent is working") is None
    assert _detect_rate_limit_in_pane("") is None


def test_pane_case_insensitive_marker() -> None:
    # Pane case may vary across Claude versions / TUI rendering.
    assert _detect_rate_limit_in_pane("YOU'VE HIT YOUR LIMIT") == ""


# --- JSONL detector --------------------------------------------------


def _write_jsonl(path: Path, lines: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")


def test_jsonl_detects_synthetic_rate_limit_event(tmp_path: Path) -> None:
    p = tmp_path / "session.jsonl"
    _write_jsonl(
        p,
        [
            # An ordinary user event upstream
            {"message": {"role": "user", "content": "hi"}},
            # The synthetic terminal-failure event Claude writes on 429
            {
                "message": {
                    "model": "<synthetic>",
                    "role": "assistant",
                    "content": [{"type": "text", "text": SYNTHETIC_TEXT}],
                },
                "error": "rate_limit",
                "isApiErrorMessage": True,
                "apiErrorStatus": 429,
            },
        ],
    )
    assert _detect_rate_limit_in_jsonl(p) == "1:30am (America/New_York)"


def test_jsonl_returns_none_for_normal_session(tmp_path: Path) -> None:
    p = tmp_path / "session.jsonl"
    _write_jsonl(
        p,
        [
            {"message": {"role": "user", "content": "hi"}},
            {
                "message": {
                    "model": "claude-opus-4-7",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "all good"}],
                },
            },
        ],
    )
    assert _detect_rate_limit_in_jsonl(p) is None


def test_jsonl_ignores_unrelated_event_with_rate_limit_string(tmp_path: Path) -> None:
    """A tool call with `rate_limit` in its content is NOT a synthetic event."""
    p = tmp_path / "session.jsonl"
    _write_jsonl(
        p,
        [
            {
                "message": {
                    "model": "claude-opus-4-7",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "discussing rate_limit code"}],
                },
            },
        ],
    )
    assert _detect_rate_limit_in_jsonl(p) is None


def test_jsonl_missing_file_returns_none(tmp_path: Path) -> None:
    assert _detect_rate_limit_in_jsonl(tmp_path / "nope.jsonl") is None


def test_jsonl_handles_garbage_lines(tmp_path: Path) -> None:
    p = tmp_path / "session.jsonl"
    p.write_text(
        "not json\n"
        + json.dumps(
            {
                "message": {
                    "model": "<synthetic>",
                    "role": "assistant",
                    "content": [{"type": "text", "text": SYNTHETIC_TEXT}],
                },
                "error": "rate_limit",
                "isApiErrorMessage": True,
                "apiErrorStatus": 429,
            }
        )
        + "\nanother garbage\n"
    )
    assert _detect_rate_limit_in_jsonl(p) == "1:30am (America/New_York)"


# --- AgentSession propagation ----------------------------------------


class _RateLimitedBackend:
    """Fake backend whose `run()` writes a synthetic-rate_limit JSONL and raises.

    Mirrors what `TmuxInteractiveClaudeBackend` does post-detection: the
    backend itself decides the turn cannot complete and raises
    `RateLimitError` before the orchestrator ever sees a verdict file.
    """

    def __init__(self, jsonl_dir: Path):
        self.jsonl_dir = jsonl_dir
        self.calls = 0

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None,
        cwd: Path,
        fork: bool = False,
        model: str = "opus",
        extra_env: Mapping[str, str] | None = None,
    ) -> tuple[str, str]:
        self.calls += 1
        # Drop the synthetic event Claude itself would write.
        sid = "11111111-2222-3333-4444-555555555555"
        jsonl = self.jsonl_dir / f"{sid}.jsonl"
        jsonl.write_text(
            json.dumps(
                {
                    "message": {
                        "model": "<synthetic>",
                        "role": "assistant",
                        "content": [{"type": "text", "text": SYNTHETIC_TEXT}],
                    },
                    "error": "rate_limit",
                    "isApiErrorMessage": True,
                    "apiErrorStatus": 429,
                }
            )
            + "\n"
        )
        # Detect from the freshly-written transcript so the assertion is
        # end-to-end across the fake "Claude wrote something / orchestrator
        # noticed it" boundary.
        reset = _detect_rate_limit_in_jsonl(jsonl)
        raise RateLimitError(reset_time=reset or None)


def test_prompt_propagates_rate_limit_error_without_nudge(tmp_path: Path) -> None:
    backend = _RateLimitedBackend(tmp_path)
    sess = AgentSession(
        role="builder",
        repo_path=tmp_path,
        backend=backend,
        skip_mail_inject=True,  # no bd shell-out in unit tests
        overlay=False,
        skills=False,
    )
    verdict_path = tmp_path / "verdicts" / "build.json"

    with pytest.raises(RateLimitError) as exc:
        sess.prompt("do the thing", expect_verdict=verdict_path)

    # AC: error carries reset-time
    assert exc.value.reset_time == "1:30am (America/New_York)"
    # AC: prompt() does NOT fire a verdict-nudge after a rate-limit
    # (would have been a second call to backend.run).
    assert backend.calls == 1
    # And the verdict file was never written, confirming the turn never
    # produced output (not even a partial one to skip nudging).
    assert not verdict_path.exists()


def test_rate_limit_error_default_message_includes_reset() -> None:
    err = RateLimitError(reset_time="3pm")
    assert "3pm" in str(err)
    assert err.reset_time == "3pm"


def test_rate_limit_error_unknown_reset() -> None:
    err = RateLimitError()
    assert err.reset_time is None
    assert "?" in str(err)
