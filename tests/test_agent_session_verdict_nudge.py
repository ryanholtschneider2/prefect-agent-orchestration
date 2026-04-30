"""Verdict-skip nudge: AgentSession re-prompts when verdict file is missing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import pytest

from prefect_orchestration.agent_session import AgentSession


@dataclass
class _OmitThenWriteBackend:
    """Backend that skips writing the verdict on turn 1, writes on turn 2.

    Mirrors the real failure mode: agent finishes analysis but forgets
    `echo {...} > $RUN_DIR/verdicts/<name>.json` on the first turn.
    """

    verdict_path: Path
    payload: dict[str, Any] = field(default_factory=lambda: {"verdict": "approved"})
    skip_first_n: int = 1
    calls: list[dict[str, Any]] = field(default_factory=list)

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None,
        cwd: Path,
        fork: bool = False,
        model: str = "opus",
        effort: str | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> tuple[str, str]:
        self.calls.append(
            {
                "prompt": prompt,
                "session_id": session_id,
                "fork": fork,
                "model": model,
            }
        )
        new_sid = session_id or f"sid-{len(self.calls)}"
        if len(self.calls) > self.skip_first_n:
            self.verdict_path.parent.mkdir(parents=True, exist_ok=True)
            self.verdict_path.write_text(json.dumps(self.payload))
        return f"ack {len(self.calls)}", new_sid


def _make_session(backend: _OmitThenWriteBackend, repo_path: Path) -> AgentSession:
    return AgentSession(
        role="triager",
        repo_path=repo_path,
        backend=backend,
        overlay=False,
        skills=False,
    )


def test_verdict_nudge_recovers_missing_file(tmp_path: Path) -> None:
    verdict = tmp_path / "verdicts" / "triage.json"
    backend = _OmitThenWriteBackend(verdict_path=verdict, skip_first_n=1)
    sess = _make_session(backend, tmp_path)

    sess.prompt("do analysis", expect_verdict=verdict)

    assert verdict.exists(), "nudge should have caused turn 2 to write the verdict"
    assert json.loads(verdict.read_text()) == {"verdict": "approved"}
    assert len(backend.calls) == 2, "exactly one nudge retry"
    nudge_prompt = backend.calls[1]["prompt"]
    assert str(verdict) in nudge_prompt
    assert "without writing" in nudge_prompt or "verdict" in nudge_prompt.lower()


def test_verdict_nudge_session_continuity(tmp_path: Path) -> None:
    """Nudge turn must reuse the post-turn session_id and not fork."""
    verdict = tmp_path / "verdicts" / "triage.json"
    backend = _OmitThenWriteBackend(verdict_path=verdict, skip_first_n=1)
    sess = _make_session(backend, tmp_path)

    sess.prompt("do analysis", expect_verdict=verdict)

    # Turn 1: session_id starts None, backend assigns "sid-1"
    assert backend.calls[0]["session_id"] is None
    assert backend.calls[0]["fork"] is False
    # Turn 2 (the nudge) must re-use sid-1 and NOT fork.
    assert backend.calls[1]["session_id"] == "sid-1"
    assert backend.calls[1]["fork"] is False


def test_verdict_nudge_still_missing_raises_loudly(tmp_path: Path) -> None:
    """If both turns omit the verdict, no infinite loop — file stays missing."""
    verdict = tmp_path / "verdicts" / "triage.json"
    # skip_first_n large enough that the nudge turn also skips the write.
    backend = _OmitThenWriteBackend(verdict_path=verdict, skip_first_n=99)
    sess = _make_session(backend, tmp_path)

    sess.prompt("do analysis", expect_verdict=verdict)

    assert not verdict.exists()
    assert len(backend.calls) == 2, "exactly one retry, no further nudges"


def test_verdict_present_skips_nudge(tmp_path: Path) -> None:
    """When the agent writes the verdict on turn 1, no nudge fires."""
    verdict = tmp_path / "verdicts" / "triage.json"
    backend = _OmitThenWriteBackend(verdict_path=verdict, skip_first_n=0)
    sess = _make_session(backend, tmp_path)

    sess.prompt("do analysis", expect_verdict=verdict)

    assert verdict.exists()
    assert len(backend.calls) == 1, "no nudge needed"


def test_verdict_nudge_skips_mail_reinjection(tmp_path: Path) -> None:
    """Mail fetcher must NOT be called again for the nudge turn."""
    verdict = tmp_path / "verdicts" / "triage.json"
    backend = _OmitThenWriteBackend(verdict_path=verdict, skip_first_n=1)

    fetch_calls: list[str] = []
    marker_calls: list[str] = []

    class _Mail:
        id = "m1"
        subject = "hi"
        body = "yo"
        from_agent = "peer"
        created_at = None

    def fetcher(role: str) -> list[Any]:
        fetch_calls.append(role)
        return [_Mail()]

    def marker(mail_id: str) -> None:
        marker_calls.append(mail_id)

    sess = AgentSession(
        role="triager",
        repo_path=tmp_path,
        backend=backend,
        overlay=False,
        skills=False,
        mail_fetcher=fetcher,
        mail_marker=marker,
    )

    sess.prompt("do analysis", expect_verdict=verdict)

    assert verdict.exists()
    # Fetcher fires once (turn 1), not again on the nudge turn.
    assert fetch_calls == ["triager"]
    # Marker fires once for the original turn's mail.
    assert marker_calls == ["m1"]
    # Nudge turn 2 prompt does NOT contain the <mail-inbox> block.
    assert "<mail-inbox>" not in backend.calls[1]["prompt"]


def test_verdict_none_preserves_legacy_behaviour(tmp_path: Path) -> None:
    """expect_verdict=None means no post-turn check, no nudge."""
    verdict = tmp_path / "verdicts" / "triage.json"
    backend = _OmitThenWriteBackend(verdict_path=verdict, skip_first_n=99)
    sess = _make_session(backend, tmp_path)

    sess.prompt("do analysis")  # no expect_verdict

    assert not verdict.exists()
    assert len(backend.calls) == 1


def test_prompt_for_verdict_recovers_via_nudge(tmp_path: Path) -> None:
    """`prompt_for_verdict` returns the parsed verdict after a nudge cycle."""
    from prefect_orchestration.parsing import prompt_for_verdict, verdicts_dir

    expected = verdicts_dir(tmp_path) / "triage.json"
    backend = _OmitThenWriteBackend(
        verdict_path=expected,
        payload={"has_ui": False},
        skip_first_n=1,
    )
    sess = _make_session(backend, tmp_path)

    out = prompt_for_verdict(sess, "do analysis", tmp_path, "triage")

    assert out == {"has_ui": False}
    assert len(backend.calls) == 2


def test_prompt_for_verdict_still_missing_raises(tmp_path: Path) -> None:
    """If the nudge also fails, prompt_for_verdict raises FileNotFoundError."""
    from prefect_orchestration.parsing import prompt_for_verdict, verdicts_dir

    expected = verdicts_dir(tmp_path) / "triage.json"
    backend = _OmitThenWriteBackend(verdict_path=expected, skip_first_n=99)
    sess = _make_session(backend, tmp_path)

    with pytest.raises(FileNotFoundError):
        prompt_for_verdict(sess, "do analysis", tmp_path, "triage")
    assert len(backend.calls) == 2
