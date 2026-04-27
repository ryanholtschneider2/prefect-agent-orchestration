"""Regression tests for prefect-orchestration-b9q.

When `TmuxInteractiveClaudeBackend` resumes a session via `--resume <prior>`,
claude generates a *new* session_id internally — the Stop hook fires with
that new id, not `<prior>`. Pre-fix the orchestrator polled
`<prior>.stopped` and wedged forever even after the agent finished.

These tests pin the discovery contract (`_discover_resumed_sentinel`):
the function must locate the right sentinel by matching cwd + post-spawn
mtime + transcript-content vs prompt, and return the *actual* session_id
claude assigned.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from prefect_orchestration.agent_session import _discover_resumed_sentinel


def _write_sentinel(
    stop_dir: Path,
    *,
    sid: str,
    cwd: Path,
    transcript: Path,
) -> Path:
    """Simulate the Stop hook firing for a session: write `<sid>.stopped`."""
    stop_dir.mkdir(parents=True, exist_ok=True)
    sentinel = stop_dir / f"{sid}.stopped"
    sentinel.write_text(
        json.dumps(
            {
                "session_id": sid,
                "transcript_path": str(transcript),
                "cwd": str(cwd.resolve()),
            }
        )
    )
    return sentinel


def _write_transcript(path: Path, prompt: str) -> Path:
    """Simulate claude writing a JSONL transcript with our prompt as user msg."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"message": {"role": "user", "content": prompt}})
    path.write_text(line + "\n")
    return path


def _has_tmux(monkeypatch) -> None:
    """Stub out tmux liveness check so the discovery loop doesn't shell out."""
    import prefect_orchestration.agent_session as agsess

    class _Result:
        returncode = 0
        stderr = b""

    monkeypatch.setattr(
        agsess.subprocess,
        "run",
        lambda *a, **kw: _Result(),  # noqa: ARG005
    )


def test_discover_resumed_sentinel_finds_new_sid_not_prior(tmp_path, monkeypatch):
    """The smoking gun: claude assigns a brand-new session_id on --resume.

    Pre-fix, the orchestrator polled `<prior>.stopped` and never found it.
    Post-fix, discovery scans by cwd+content and returns the new id.
    """
    _has_tmux(monkeypatch)
    stop_dir = tmp_path / "po-stops"
    cwd = tmp_path / "rig"
    cwd.mkdir()

    PRIOR = "11111111-1111-1111-1111-111111111111"
    NEW = "22222222-2222-2222-2222-222222222222"
    PROMPT = (
        "You are the **builder** for issue prefect-orchestration-b9q.\n"
        "UNIQUE-MARKER-FOR-THIS-SPAWN-XYZ\n"
    )

    transcript = (
        tmp_path / ".claude" / "projects" / "rig-slug" / f"{NEW}.jsonl"
    )
    _write_transcript(transcript, PROMPT)
    _write_sentinel(stop_dir, sid=NEW, cwd=cwd, transcript=transcript)

    sid, found = _discover_resumed_sentinel(
        stop_dir,
        cwd,
        PROMPT,
        spawn_start=time.time() - 60,
        session_name="po-test",
        timeout=2.0,
    )

    assert sid == NEW
    assert sid != PRIOR  # the bug
    assert found == transcript


def test_discover_resumed_sentinel_ignores_other_rigs(tmp_path, monkeypatch):
    """Sentinel from a different rig in the same stop_dir must not match."""
    _has_tmux(monkeypatch)
    stop_dir = tmp_path / "po-stops"
    our_cwd = tmp_path / "our-rig"
    our_cwd.mkdir()
    other_cwd = tmp_path / "other-rig"
    other_cwd.mkdir()

    PROMPT = "You are the builder. SHARED-ROLE-PREAMBLE."

    # A sentinel from another rig firing with the SAME prompt prefix —
    # cwd mismatch must reject it.
    other_transcript = tmp_path / "other.jsonl"
    _write_transcript(other_transcript, PROMPT)
    _write_sentinel(
        stop_dir,
        sid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        cwd=other_cwd,
        transcript=other_transcript,
    )

    with pytest.raises(TimeoutError):
        _discover_resumed_sentinel(
            stop_dir,
            our_cwd,
            PROMPT,
            spawn_start=time.time() - 60,
            session_name="po-test",
            timeout=0.5,
        )


def test_discover_resumed_sentinel_ignores_pre_spawn_sentinels(tmp_path, monkeypatch):
    """A sentinel from an earlier turn (mtime < spawn_start) must not match."""
    _has_tmux(monkeypatch)
    stop_dir = tmp_path / "po-stops"
    cwd = tmp_path / "rig"
    cwd.mkdir()

    PROMPT = "Same prompt, different turn — UNIQUE-MARKER"
    OLD_SID = "33333333-3333-3333-3333-333333333333"

    transcript = tmp_path / "old.jsonl"
    _write_transcript(transcript, PROMPT)
    sentinel = _write_sentinel(
        stop_dir, sid=OLD_SID, cwd=cwd, transcript=transcript
    )
    # Backdate the sentinel.
    old_ts = time.time() - 3600
    import os

    os.utime(sentinel, (old_ts, old_ts))

    # spawn_start is "now" — this is a *new* turn that hasn't completed yet.
    with pytest.raises(TimeoutError):
        _discover_resumed_sentinel(
            stop_dir,
            cwd,
            PROMPT,
            spawn_start=time.time(),
            session_name="po-test",
            timeout=0.5,
        )


def test_discover_resumed_sentinel_disambiguates_by_prompt(tmp_path, monkeypatch):
    """Two concurrent agents in the same rig — picks the one whose
    transcript matches our prompt."""
    _has_tmux(monkeypatch)
    stop_dir = tmp_path / "po-stops"
    cwd = tmp_path / "rig"
    cwd.mkdir()

    OUR_PROMPT = "You are the builder. UNIQUE-FOR-BUILDER-X1Y2Z3."
    THEIR_PROMPT = "You are the critic. UNIQUE-FOR-CRITIC-A4B5C6."
    OUR_SID = "44444444-4444-4444-4444-444444444444"
    THEIR_SID = "55555555-5555-5555-5555-555555555555"

    our_transcript = tmp_path / "ours.jsonl"
    _write_transcript(our_transcript, OUR_PROMPT)
    their_transcript = tmp_path / "theirs.jsonl"
    _write_transcript(their_transcript, THEIR_PROMPT)

    _write_sentinel(stop_dir, sid=OUR_SID, cwd=cwd, transcript=our_transcript)
    _write_sentinel(
        stop_dir, sid=THEIR_SID, cwd=cwd, transcript=their_transcript
    )

    sid, found = _discover_resumed_sentinel(
        stop_dir,
        cwd,
        OUR_PROMPT,
        spawn_start=time.time() - 60,
        session_name="po-test",
        timeout=2.0,
    )
    assert sid == OUR_SID
    assert found == our_transcript


def test_discover_resumed_sentinel_raises_when_tmux_dies(tmp_path, monkeypatch):
    """If the tmux session disappears before any matching sentinel appears,
    surface a RuntimeError (not a silent forever-spin)."""
    import prefect_orchestration.agent_session as agsess

    class _Dead:
        returncode = 1
        stderr = b"can't find session"

    monkeypatch.setattr(
        agsess.subprocess, "run", lambda *a, **kw: _Dead()  # noqa: ARG005
    )

    stop_dir = tmp_path / "po-stops"
    stop_dir.mkdir()
    cwd = tmp_path / "rig"
    cwd.mkdir()

    with pytest.raises(RuntimeError, match="disappeared before Stop hook"):
        _discover_resumed_sentinel(
            stop_dir,
            cwd,
            "any prompt",
            spawn_start=time.time(),
            session_name="po-dead",
            timeout=2.0,
        )


def test_discover_resumed_sentinel_handles_missing_transcript(tmp_path, monkeypatch):
    """A sentinel pointing at a transcript that hasn't been written to disk
    yet (race) must not match — keep polling, don't crash."""
    _has_tmux(monkeypatch)
    stop_dir = tmp_path / "po-stops"
    cwd = tmp_path / "rig"
    cwd.mkdir()

    PROMPT = "Test prompt with marker FOO"

    # Sentinel exists but transcript doesn't — should be skipped.
    ghost_transcript = tmp_path / "does-not-exist.jsonl"
    _write_sentinel(
        stop_dir,
        sid="66666666-6666-6666-6666-666666666666",
        cwd=cwd,
        transcript=ghost_transcript,
    )

    with pytest.raises(TimeoutError):
        _discover_resumed_sentinel(
            stop_dir,
            cwd,
            PROMPT,
            spawn_start=time.time() - 60,
            session_name="po-test",
            timeout=0.4,
        )


def test_agent_session_persists_new_sid_after_resume(tmp_path):
    """Contract test: when a backend returns a NEW session_id (as happens
    on `--resume <prior>` because claude generates fresh ids internally),
    `AgentSession` must persist the new id for the next turn.

    Pre-fix the orchestrator was relying on `new_sid == prior` and would
    keep resuming a stale id. The discovery fix returns the actual new id
    from `backend.run`; this test pins that AgentSession threads it
    through correctly.
    """
    from prefect_orchestration.agent_session import AgentSession

    seen_session_ids: list[str | None] = []

    class _ResumeChangesSidBackend:
        """Mimics what real claude does: each turn returns a fresh sid,
        regardless of what `session_id` was passed in."""

        def __init__(self):
            self._counter = 0

        def run(self, prompt, *, session_id, cwd, fork=False, model="opus", extra_env=None):
            seen_session_ids.append(session_id)
            self._counter += 1
            return f"turn-{self._counter}-result", f"new-sid-{self._counter}"

    sess = AgentSession(
        role="builder",
        repo_path=tmp_path,
        backend=_ResumeChangesSidBackend(),
        skip_mail_inject=True,
        overlay=False,
        skills=False,
    )

    # First turn: no prior session_id.
    sess.prompt("turn 1")
    assert sess.session_id == "new-sid-1"

    # Second turn: AgentSession should pass the new id from turn 1 as the
    # resume target, then accept whatever new id the backend returns.
    sess.prompt("turn 2")
    assert sess.session_id == "new-sid-2"

    # Third turn: same dance.
    sess.prompt("turn 3")
    assert sess.session_id == "new-sid-3"

    # Backend received the right `session_id` arg each time:
    #   turn 1 → None (fresh)
    #   turn 2 → new-sid-1 (resume target from turn 1)
    #   turn 3 → new-sid-2 (resume target from turn 2)
    assert seen_session_ids == [None, "new-sid-1", "new-sid-2"]


def test_discover_resumed_sentinel_handles_corrupt_sentinel(tmp_path, monkeypatch):
    """Malformed JSON sentinel (partial write race) must be skipped, not
    crash the discovery loop."""
    _has_tmux(monkeypatch)
    stop_dir = tmp_path / "po-stops"
    stop_dir.mkdir()
    cwd = tmp_path / "rig"
    cwd.mkdir()

    bad_sentinel = stop_dir / "bad.stopped"
    bad_sentinel.write_text("{not-json")

    PROMPT = "any"

    # Should TimeoutError (no valid match), not raise JSONDecodeError.
    with pytest.raises(TimeoutError):
        _discover_resumed_sentinel(
            stop_dir,
            cwd,
            PROMPT,
            spawn_start=time.time() - 60,
            session_name="po-test",
            timeout=0.4,
        )
