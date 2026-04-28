"""Tests for TmuxClaudeBackend (prefect-orchestration-64y).

Unit tests cover argv construction, envelope parsing, and env scrubbing
without spawning tmux. Integration tests (gated on `tmux` binary) use a
fake `claude` shim script so we exercise the real tmux spawn/teardown
flow without burning API calls.
"""

from __future__ import annotations

import json
import shlex
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

from prefect_orchestration.agent_session import (
    ClaudeCliBackend,
    SessionBackend,
    TmuxClaudeBackend,
    _build_claude_argv,
    _clean_env,
    _parse_envelope,
)


# ---------------------------------------------------------------------------
# Unit tests (no tmux required)
# ---------------------------------------------------------------------------


def test_tmux_backend_satisfies_protocol():
    backend = TmuxClaudeBackend(issue="abc", role="builder")
    # Structural/duck check — Protocol is runtime_checkable=False here.
    assert hasattr(backend, "run")
    assert callable(backend.run)
    # AgentSession uses the SessionBackend Protocol; any callable .run with
    # the right kwargs satisfies it at type-check time.
    _: SessionBackend = backend  # noqa: F841


def test_build_claude_argv_matches_legacy_no_resume():
    argv = _build_claude_argv(
        "claude --dangerously-skip-permissions", None, False, "opus"
    )
    assert argv == [
        "claude",
        "--dangerously-skip-permissions",
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--model",
        "opus",
        "--setting-sources",
        "project,local",
    ]


def test_build_claude_argv_with_resume():
    sid = "12345678-1234-1234-1234-1234567890ab"
    argv = _build_claude_argv("claude", sid, False, "sonnet")
    assert argv[-2:] == ["--resume", sid]
    assert "--setting-sources" in argv
    assert argv[argv.index("--setting-sources") + 1] == "project,local"


def test_build_claude_argv_with_fork():
    sid = "12345678-1234-1234-1234-1234567890ab"
    argv = _build_claude_argv("claude", sid, True, "opus")
    assert argv[-1] == "--fork-session"
    assert "--resume" in argv


def test_build_claude_argv_skips_user_settings():
    """Pin: PO must skip user-level ~/.claude/settings.json so the
    bd-prime SessionStart hook can't fan-out-contend on the dolt-server
    backend (was failing 16/21 of a parallel po-resume wave)."""
    argv = _build_claude_argv("claude", None, False, "opus")
    assert "--setting-sources" in argv
    assert argv[argv.index("--setting-sources") + 1] == "project,local"


def test_build_claude_argv_ignores_non_uuid_sid():
    argv = _build_claude_argv("claude", "stub-abcdef", False, "opus")
    assert "--resume" not in argv


def test_parse_envelope_valid_json():
    payload = json.dumps({"session_id": "new-sid", "result": "hello"})
    result, sid = _parse_envelope(payload, "prior")
    assert result == "hello"
    assert sid == "new-sid"


def test_parse_envelope_invalid_json_falls_back():
    result, sid = _parse_envelope("not json", "prior-sid")
    assert result == "not json"
    assert sid == "prior-sid"


def test_clean_env_strips_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-stripped")
    env = _clean_env()
    assert "ANTHROPIC_API_KEY" not in env


def test_session_name_derivation():
    backend = TmuxClaudeBackend(issue="sr-8yu.3", role="builder")
    assert backend._session_name() == "po-sr-8yu.3-builder"


def test_tmux_missing_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    backend = TmuxClaudeBackend(issue="x", role="y")
    with pytest.raises(RuntimeError, match="tmux"):
        backend.run("hi", session_id=None, cwd=tmp_path)


def test_claude_cli_backend_argv_unchanged(monkeypatch):
    """Regression: factoring out _build_claude_argv must not change the
    argv ClaudeCliBackend shells out with."""
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        from subprocess import CompletedProcess

        return CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=json.dumps({"session_id": "new", "result": "ok"}),
            stderr="",
        )

    monkeypatch.setattr("prefect_orchestration.agent_session.subprocess.run", fake_run)
    backend = ClaudeCliBackend()
    backend.run("hi", session_id=None, cwd=Path("/tmp"))
    assert captured["cmd"] == [
        "claude",
        "--dangerously-skip-permissions",
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--model",
        "opus",
        "--setting-sources",
        "project,local",
    ]


# ---------------------------------------------------------------------------
# Integration tests (require tmux + bash; use a fake `claude` shim script)
# ---------------------------------------------------------------------------


_TMUX_AVAILABLE = shutil.which("tmux") is not None and shutil.which("bash") is not None
requires_tmux = pytest.mark.skipif(
    not _TMUX_AVAILABLE, reason="tmux/bash not available"
)


def _make_shim(tmp_path: Path, body: str) -> str:
    """Write an executable shell script that impersonates `claude`.

    Returns a start_command usable as TmuxClaudeBackend(start_command=...)
    — a quoted path, so the backend's `shlex.split` parses it correctly.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    shim = tmp_path / "fake_claude.sh"
    shim.write_text("#!/usr/bin/env bash\n" + body)
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return shlex.quote(str(shim))


@requires_tmux
def test_tmux_backend_returns_parsed_envelope(tmp_path):
    shim = _make_shim(
        tmp_path,
        textwrap.dedent(
            """\
            # ignore all args; just emit the envelope
            cat >/dev/null   # drain stdin so the pipeline doesn't SIGPIPE
            echo '{"session_id": "11111111-1111-1111-1111-111111111111", "result": "hello from shim"}'
            """
        ),
    )
    backend = TmuxClaudeBackend(
        issue="testcase",
        role="builder",
        start_command=shim,
        attach_hint=False,
        timeout_s=15,
    )
    result, sid = backend.run("prompt body", session_id=None, cwd=tmp_path)
    assert result == "hello from shim"
    assert sid == "11111111-1111-1111-1111-111111111111"


@requires_tmux
def test_tmux_backend_writes_verdict_file(tmp_path):
    """AC 4: verdict files still land in run_dir/verdicts/."""
    shim = _make_shim(
        tmp_path,
        textwrap.dedent(
            """\
            cat >/dev/null
            mkdir -p verdicts
            echo '{"verdict":"approved"}' > verdicts/plan.json
            echo '{"session_id": "22222222-2222-2222-2222-222222222222", "result": "ok"}'
            """
        ),
    )
    backend = TmuxClaudeBackend(
        issue="verdicttest",
        role="planner",
        start_command=shim,
        attach_hint=False,
        timeout_s=15,
    )
    backend.run("prompt", session_id=None, cwd=tmp_path)
    assert (tmp_path / "verdicts" / "plan.json").exists()
    assert json.loads((tmp_path / "verdicts" / "plan.json").read_text()) == {
        "verdict": "approved"
    }


@requires_tmux
def test_tmux_backend_cleans_up_session(tmp_path):
    """AC 6: tmux session exits cleanly (no orphans)."""
    shim = _make_shim(
        tmp_path,
        textwrap.dedent(
            """\
            cat >/dev/null
            echo '{"session_id": "33333333-3333-3333-3333-333333333333", "result": "ok"}'
            """
        ),
    )
    backend = TmuxClaudeBackend(
        issue="cleanup",
        role="builder",
        start_command=shim,
        attach_hint=False,
        timeout_s=15,
    )
    backend.run("prompt", session_id=None, cwd=tmp_path)
    # Session must be gone
    has = subprocess.run(
        ["tmux", "has-session", "-t", "po-cleanup-builder"],
        capture_output=True,
        check=False,
    )
    assert has.returncode != 0


@requires_tmux
def test_tmux_backend_passes_resume_across_turns(tmp_path):
    """AC 5: --resume <uuid> works across turns.

    The shim captures its own argv to a file. First turn: no resume. We
    feed the backend a session_id via the envelope; second turn should
    include `--resume <uuid>` in argv.
    """
    turn1_log = tmp_path / "turn1.args"
    turn2_log = tmp_path / "turn2.args"

    shim1 = _make_shim(
        tmp_path / "t1",
        textwrap.dedent(
            f"""\
            cat >/dev/null
            printf '%s\\n' "$@" > {shlex.quote(str(turn1_log))}
            echo '{{"session_id": "44444444-4444-4444-4444-444444444444", "result": "ok"}}'
            """
        ),
    )
    backend1 = TmuxClaudeBackend(
        issue="resume",
        role="builder",
        start_command=shim1,
        attach_hint=False,
        timeout_s=15,
    )
    _, sid1 = backend1.run("turn 1", session_id=None, cwd=tmp_path)

    shim2 = _make_shim(
        tmp_path / "t2",
        textwrap.dedent(
            f"""\
            cat >/dev/null
            printf '%s\\n' "$@" > {shlex.quote(str(turn2_log))}
            echo '{{"session_id": "55555555-5555-5555-5555-555555555555", "result": "ok"}}'
            """
        ),
    )
    backend2 = TmuxClaudeBackend(
        issue="resume",
        role="builder",
        start_command=shim2,
        attach_hint=False,
        timeout_s=15,
    )
    _, sid2 = backend2.run("turn 2", session_id=sid1, cwd=tmp_path)

    t1 = turn1_log.read_text()
    t2 = turn2_log.read_text()
    assert "--resume" not in t1
    assert "--resume" in t2
    assert sid1 in t2
    assert sid2 == "55555555-5555-5555-5555-555555555555"


@requires_tmux
def test_tmux_backend_propagates_nonzero_exit(tmp_path):
    shim = _make_shim(
        tmp_path,
        textwrap.dedent(
            """\
            cat >/dev/null
            echo "boom" >&2
            exit 7
            """
        ),
    )
    backend = TmuxClaudeBackend(
        issue="fail",
        role="builder",
        start_command=shim,
        attach_hint=False,
        timeout_s=15,
    )
    with pytest.raises(RuntimeError, match="exited 7"):
        backend.run("prompt", session_id=None, cwd=tmp_path)

    # Even on failure, no orphan session.
    has = subprocess.run(
        ["tmux", "has-session", "-t", "po-fail-builder"],
        capture_output=True,
        check=False,
    )
    assert has.returncode != 0


@requires_tmux
def test_tmux_backend_kills_preexisting_session(tmp_path):
    # Pre-seed a tmux session with the same name.
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", "po-preexisting-builder", "sleep", "600"],
        check=True,
        env=_clean_env(),
    )
    try:
        shim = _make_shim(
            tmp_path,
            textwrap.dedent(
                """\
                cat >/dev/null
                echo '{"session_id": "55555555-5555-5555-5555-555555555555", "result": "ok"}'
                """
            ),
        )
        backend = TmuxClaudeBackend(
            issue="preexisting",
            role="builder",
            start_command=shim,
            attach_hint=False,
            timeout_s=15,
        )
        result, _ = backend.run("prompt", session_id=None, cwd=tmp_path)
        assert result == "ok"
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", "po-preexisting-builder"],
            check=False,
            stderr=subprocess.DEVNULL,
        )


# ─── sav.3: sleep-infinity is conditional on non-zero claude exit ─────


def test_interactive_wrapper_sleep_infinity_only_on_failure():
    """sav.3 regression: `sleep infinity` must be guarded on rc != 0.

    The prior unconditional `<cmd> ; sleep infinity` left zombie tmux
    sessions + claude children whenever the parent `po` process died,
    holding rate-limit slots indefinitely. The new shape only sleeps
    when claude exited abnormally, so clean exits collapse the pane.
    """
    import inspect

    from prefect_orchestration.agent_session import TmuxInteractiveClaudeBackend

    src = inspect.getsource(TmuxInteractiveClaudeBackend.run)
    assert '"$rc" -ne 0' in src
    # Legacy unconditional shape must be gone.
    assert ' ; sleep infinity"' not in src
