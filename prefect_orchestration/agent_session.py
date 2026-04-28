"""Claude Code CLI session abstraction.

Designed so a Prefect task — or later a Temporal activity — can:

  * start a fresh role session (planner, critic, builder, ...)
  * resume the same session to keep accumulated context cheap
  * fork the session for a branch that shouldn't pollute the parent
  * retrieve the session_id so the orchestrator can persist it as
    flow/workflow state

Uses the `claude` CLI by default (OAuth via the Claude.ai subscription).
For a commercial deployment, swap `ClaudeCliBackend` for a backend that
calls the Claude Agent SDK with `ANTHROPIC_API_KEY`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from prefect_orchestration.secrets import (
    DEFAULT_PREFIXES,
    SecretProvider,
)

logger = logging.getLogger(__name__)

MAX_INBOX_MESSAGES = 20

# Default wall-clock cap on a single Claude turn before we declare the
# session wedged and bail. 30 min is generous enough for deep cogitation,
# long plans, ralph loops, and verifier passes — but short enough that a
# rate-limited / hung session surfaces as an error instead of polling the
# sentinel forever (sav.1: storybook flow hung 12+ hours after Claude hit
# Anthropic's rate limit at 02:47Z because timeout_s defaulted to None).
DEFAULT_AGENT_TIMEOUT_S = 1800.0

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class RateLimitError(RuntimeError):
    """Claude returned a rate-limit (429) for this turn — terminal, not retryable in-loop.

    Raised by interactive backends when the pane shows ``You've hit your
    limit`` or the JSONL transcript contains the synthetic
    ``error: "rate_limit"`` assistant event. ``reset_time`` is the
    human-readable reset string Claude surfaces (e.g.
    ``"1:30am (America/New_York)"``); ``None`` if the message was detected
    but the time substring couldn't be parsed.

    Callers (`po retry`, beadsd worker) inspect ``reset_time`` to decide
    whether to defer + retry or fail loudly. AgentSession.prompt() lets
    this propagate without firing the verdict-nudge retry — the turn
    didn't fail to write a verdict, it never ran.
    """

    def __init__(self, reset_time: str | None = None, message: str | None = None):
        self.reset_time = reset_time or None
        super().__init__(
            message or f"Claude rate-limit hit; resets {self.reset_time or '?'}"
        )


# `resets <time>` capture stops at end-of-line, closing-paren, or middle-dot
# so we don't slurp trailing punctuation. Claude's exact format is
# `You've hit your limit · resets 1:30am (America/New_York)`.
_RATE_LIMIT_RESET_RE = re.compile(r"resets\s+([^\n·]+)", re.IGNORECASE)
_RATE_LIMIT_MARKERS = (
    "you've hit your limit",
    "you have hit your limit",
    "rate limit",
)


def _extract_reset_time(text: str) -> str:
    """Return the parsed reset-time substring (no parens), or '' if absent."""
    m = _RATE_LIMIT_RESET_RE.search(text)
    return m.group(1).strip() if m else ""


def _detect_rate_limit_in_pane(pane_text: str) -> str | None:
    """Return reset-time (possibly '') if the tmux pane shows a rate-limit; else None.

    Empty-string return means the marker was found but no parseable reset
    time; ``None`` means no rate-limit at all. Callers must distinguish:
    empty string is still a rate-limit, just a less-informative one.
    """
    lower = pane_text.lower()
    if not any(m in lower for m in _RATE_LIMIT_MARKERS):
        return None
    return _extract_reset_time(pane_text)


def _detect_rate_limit_in_jsonl(jsonl_path: Path) -> str | None:
    """Scan a Claude JSONL transcript for the synthetic rate_limit event.

    Claude itself writes a deterministic terminal-failure marker when a
    turn hits the API rate limit: an assistant event with
    ``model="<synthetic>"``, top-level ``error="rate_limit"``,
    ``isApiErrorMessage=true``, ``apiErrorStatus=429``, and a single text
    block whose body is the user-facing ``You've hit your limit · resets
    <time>`` string.

    Returns reset-time substring (possibly '') if found, else ``None``.
    """
    if not jsonl_path.exists():
        return None
    try:
        raw_text = jsonl_path.read_text()
    except OSError:
        return None
    for raw in raw_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("error") != "rate_limit" and ev.get("apiErrorStatus") != 429:
            continue
        msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
        # Defence-in-depth: only trust this marker when it's clearly the
        # synthetic assistant event, not e.g. a tool call whose content
        # happens to mention "rate_limit".
        if msg.get("model") != "<synthetic>" and not ev.get("isApiErrorMessage"):
            continue
        text = ""
        for block in msg.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text") or ""
                break
        return _extract_reset_time(text)
    return None


class SessionBackend(Protocol):
    """A backend that can run a prompt in a session and return output + new session_id.

    `extra_env`, when provided, is the role-scoped subset of secrets the
    backend should inject into the child process's environment (already
    re-keyed — e.g. `{"SLACK_TOKEN": "xoxb-…"}`). Backends are
    responsible for scrubbing any peer-role scoped vars from the base
    env before overlaying.
    """

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None,
        cwd: Path,
        fork: bool = False,
        model: str = "opus",
        extra_env: Mapping[str, str] | None = None,
    ) -> tuple[str, str]: ...


def _build_claude_argv(
    start_command: str,
    session_id: str | None,
    fork: bool,
    model: str,
) -> list[str]:
    """Construct the `claude --print ...` argv shared by all backends.

    Uses `stream-json` so a human attaching to the tmux session sees
    the agent's thoughts and tool calls live, not a blank pane that
    fills in only at completion. `--verbose` is required by the CLI
    when combining `--print` with `--output-format stream-json`.
    """
    argv = shlex.split(start_command) + [
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--model",
        model,
        # Skip user-level settings (~/.claude/settings.json). PO fans
        # out many parallel claude subprocesses; if the user-level
        # SessionStart hook shells out to a contended resource (e.g.
        # `bd prime` against a single dolt-server), 20+ concurrent
        # hook invocations kill each other and claude exits 1
        # mid-startup. Project + local settings still load — PO writes
        # its Stop hook to .claude/settings.local.json, which lives
        # under "local".
        "--setting-sources",
        "project,local",
    ]
    if session_id and _UUID_RE.match(session_id):
        argv += ["--resume", session_id]
        if fork:
            argv.append("--fork-session")
    return argv


def _parse_envelope(stdout: str, prior_sid: str | None) -> tuple[str, str]:
    """Parse a stream-json event log: return (final_result, session_id).

    `stream-json` emits one JSON object per line. The final `type=result`
    event has the same `session_id` + `result` shape that the legacy
    single-blob `--output-format json` produced; everything else is for
    human lurking. Falls back to single-blob parse when no `result` event
    is seen (covers older runs / mocks).
    """
    last_result: dict | None = None
    last_session_id = prior_sid
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if "session_id" in event and isinstance(event["session_id"], str):
            last_session_id = event["session_id"]
        if event.get("type") == "result":
            last_result = event
    if last_result is None:
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError:
            return stdout, last_session_id or str(uuid.uuid4())
        new_sid = envelope.get("session_id") or last_session_id or str(uuid.uuid4())
        return envelope.get("result", stdout), new_sid
    new_sid = last_result.get("session_id") or last_session_id or str(uuid.uuid4())
    return last_result.get("result", stdout), new_sid


@dataclass
class ClaudeCliBackend:
    """Shells out to `claude` with `--resume` / `--fork-session`.

    Notes:
        * `--print` is used so the CLI runs non-interactively and exits
        * `--output-format json` gives us a stable envelope with
          `session_id` and `result` fields
        * `--dangerously-skip-permissions` matches the pack's default
          for pool agents
    """

    start_command: str = "claude --dangerously-skip-permissions"

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
        cmd = _build_claude_argv(self.start_command, session_id, fork, model)

        proc = subprocess.run(
            cmd,
            input=prompt,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            env=_clean_env(extra_env),
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI exited {proc.returncode}\n"
                f"argv: {cmd}\n"
                f"stderr: {proc.stderr[:2000]}\n"
                f"stdout: {proc.stdout[:2000]}"
            )

        return _parse_envelope(proc.stdout, session_id)


_STUB_VERDICTS: dict[str, dict] = {
    "triage": {
        "has_ui": False,
        "has_backend": True,
        "needs_migration": False,
        "is_docs_only": False,
    },
    "plan": {"verdict": "approved"},
    "review": {"verdict": "approved"},
    "verification": {"verdict": "approved"},
    "regression": {"regression_detected": False},
    "ralph": {"ralph_found_improvement": False},
    "test": {"passed": True, "count": 7},
    "full-test-gate": {"passed": True, "failures": [], "summary": "stub"},
}


@dataclass
class StubBackend:
    """Exercise the flow DAG with no real Claude calls.

    Writes the verdict file the real agent would have written so the
    orchestrator's file reads succeed and the flow advances. Sniffs
    the prompt for two things: which verdict artifact to emit, and
    what filename to write it to (we extract it from the literal
    `{{run_dir}}/verdicts/<name>.json` path in the prompt).
    """

    captured_extra_env: dict[str, dict[str, str]] = field(default_factory=dict)
    """Last `extra_env` seen, keyed by session_id. Test hook only."""

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
        import re as _re

        sid = session_id or f"stub-{uuid.uuid4().hex[:8]}"
        self.captured_extra_env[sid] = dict(extra_env or {})
        # Find the first `<path>/verdicts/<name>.json` in the prompt
        # Prefer the `cat > <path> <<EOF` form (unambiguous). Fall back
        # to any absolute-path occurrence if not found.
        match = _re.search(r"cat\s*>\s*(/[^\s`]+/verdicts/[\w\-.]+\.json)", prompt)
        if not match:
            match = _re.search(r"(/[^\s`]+/verdicts/[\w\-.]+\.json)", prompt)
        if match:
            verdict_path = Path(match.group(1))
            verdict_path.parent.mkdir(parents=True, exist_ok=True)
            # Pick the right stub payload based on verdict filename
            stem = verdict_path.stem  # e.g. "review-iter-1"
            key = stem.split("-iter-")[0] if "-iter-" in stem else stem
            if key not in _STUB_VERDICTS and key in ("unit", "e2e", "playwright"):
                key = "test"
            payload = _STUB_VERDICTS.get(key, {"ok": True})
            verdict_path.write_text(json.dumps(payload))
        return "[dry-run] ack", sid


def _clean_env(extra_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Env for Claude CLI subprocesses.

    Strip `ANTHROPIC_API_KEY` so the CLI uses OAuth (matches the
    user-global convention), then strip every `<PREFIX>_*` role-scoped
    secret so peer-role tokens don't leak through `os.environ`. If
    `extra_env` is supplied, overlay it last — this is the current
    role's re-keyed secrets (e.g. `{"SLACK_TOKEN": "xoxb-…"}`).
    """
    from prefect_orchestration.secrets import strip_role_scoped

    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    strip_role_scoped(env, DEFAULT_PREFIXES)
    if extra_env:
        env.update(extra_env)
    return env


def _wait_for_rc(
    rc_path: Path,
    session_name: str,
    *,
    timeout: float | None = None,
    poll: float = 0.2,
) -> None:
    """Block until the wrapper writes an rc file, or raise.

    `timeout=None` means wait indefinitely — agent turns can legitimately
    run for hours (long plans, deep critiques, ralph iterations). Caller
    cancels by killing the tmux session, which this loop detects.

    Also bails if the tmux session disappears before the rc file appears
    (e.g. someone `tmux kill-session`'d it mid-run).
    """
    deadline = time.monotonic() + timeout if timeout is not None else None
    while deadline is None or time.monotonic() < deadline:
        if rc_path.exists() and rc_path.stat().st_size > 0:
            return
        has = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True,
            check=False,
        )
        if has.returncode != 0 and not rc_path.exists():
            raise RuntimeError(
                f"tmux session {session_name!r} disappeared before writing rc"
            )
        time.sleep(poll)
    raise TimeoutError(
        f"claude CLI in tmux session {session_name!r} did not finish within {timeout}s"
    )


def _spawn_tmux(
    *,
    session_name: str,
    window_name: str | None,
    wrapper: str,
    env: Mapping[str, str],
    cwd: Path,
    geometry: tuple[int, int],
) -> str:
    """Create a tmux session (or window inside a shared session); return target spec.

    Returns an opaque target string suitable for `tmux <cmd> -t <target>`:
    the session name when `window_name is None`, or `@<window_id>` when
    scoped — addressing by window id survives same-name collisions if a
    later spawn renames or reuses the slot.

    Pre-existing artifacts with the same identity are killed first
    (crash recovery): same-named session in unscoped mode, same-named
    window inside the shared session in scoped mode.
    """
    width, height = geometry
    if window_name:
        # Scoped: kill stale same-named window (no-op if missing or no session).
        subprocess.run(
            ["tmux", "kill-window", "-t", f"{session_name}:{window_name}"],
            capture_output=True,
            check=False,
        )
        has_session = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True,
            check=False,
        )
        if has_session.returncode == 0:
            proc = subprocess.run(
                [
                    "tmux",
                    "new-window",
                    "-t",
                    session_name,
                    "-n",
                    window_name,
                    "-P",
                    "-F",
                    "#{window_id}",
                    "bash",
                    "-lc",
                    wrapper,
                ],
                check=True,
                capture_output=True,
                env=dict(env),
                cwd=cwd,
            )
        else:
            proc = subprocess.run(
                [
                    "tmux",
                    "new-session",
                    "-d",
                    "-s",
                    session_name,
                    "-n",
                    window_name,
                    "-x",
                    str(width),
                    "-y",
                    str(height),
                    "-P",
                    "-F",
                    "#{window_id}",
                    "bash",
                    "-lc",
                    wrapper,
                ],
                check=True,
                capture_output=True,
                env=dict(env),
                cwd=cwd,
            )
        wid = proc.stdout.decode().strip()
        if not wid.startswith("@"):
            raise RuntimeError(f"tmux returned unexpected window id: {wid!r}")
        from prefect_orchestration import tmux_tracker

        tmux_tracker.register(
            tmux_tracker.TmuxRef(
                session_name=session_name, window_name=window_name, target=wid
            )
        )
        return wid

    # Unscoped: dedicated session per (issue, role) — old behaviour.
    kill = subprocess.run(
        ["tmux", "kill-session", "-t", session_name],
        capture_output=True,
        check=False,
    )
    if kill.returncode == 0:
        print(
            f"[tmux] killed pre-existing session {session_name!r}",
            file=sys.stderr,
            flush=True,
        )
    subprocess.run(
        [
            "tmux",
            "new-session",
            "-d",
            "-s",
            session_name,
            "-x",
            str(width),
            "-y",
            str(height),
            "bash",
            "-lc",
            wrapper,
        ],
        check=True,
        env=dict(env),
        cwd=cwd,
    )
    from prefect_orchestration import tmux_tracker

    tmux_tracker.register(
        tmux_tracker.TmuxRef(
            session_name=session_name, window_name=None, target=session_name
        )
    )
    return session_name


def _cleanup_tmux(target: str, *, scoped: bool) -> None:
    """Tear down whatever `_spawn_tmux` returned.

    In scoped mode this kills only our window (peer roles in the same
    session keep running). In unscoped mode it kills the whole session,
    matching pre-scope behaviour.
    """
    cmd = ["tmux", "kill-window" if scoped else "kill-session", "-t", target]
    subprocess.run(
        cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    from prefect_orchestration import tmux_tracker

    tmux_tracker.unregister_by_target(target)


@dataclass
class TmuxClaudeBackend:
    """Spawn `claude --print` inside a detached tmux session.

    Lets a human attach mid-run to watch the agent's live output.
    Preserves the `ClaudeCliBackend` contract: same argv, same
    JSON-envelope parsing, verdict files land in `cwd/verdicts/`
    exactly as before.

    Two layout modes, controlled by `scope`:

    * `scope=None` (back-compat): one tmux session per (issue, role)
      named `po-{issue}-{role}`. Attach: `tmux attach -t po-{issue}-{role}`.
    * `scope="<rig>" or "<rig>-<epic>"`: one shared session named
      `po-{scope}` with a window per role spawn named `{issue}-{role}`.
      Attach: `tmux attach -t po-{scope}` (cycle windows with `C-b w`).

    In both cases, pre-existing artifacts with the same identity are
    killed and replaced (prior run probably crashed).
    """

    issue: str
    role: str
    start_command: str = "claude --dangerously-skip-permissions"
    attach_hint: bool = True
    # Finite default (sav.1) — see DEFAULT_AGENT_TIMEOUT_S. Set to a larger
    # value or None on the dataclass instance for unusually long turns.
    timeout_s: float | None = DEFAULT_AGENT_TIMEOUT_S
    scope: str | None = None

    def _session_name(self, suffix: str = "") -> str:
        # Delegated to `attach.session_name` so `po attach` and the live
        # `TmuxClaudeBackend` agree byte-for-byte on the naming rule.
        from prefect_orchestration.attach import session_name as _session_name_for

        base = _session_name_for(self.issue, self.role)
        return f"{base}-{suffix}" if suffix else base

    def _scoped_names(self, suffix: str = "") -> tuple[str, str]:
        """Return (session_name, window_name) for the scoped layout."""
        assert self.scope is not None
        safe_scope = self.scope.replace(".", "_")
        safe_issue = self.issue.replace(".", "_")
        safe_role = self.role.replace(".", "_")
        session = f"po-{safe_scope}"
        window = f"{safe_issue}-{safe_role}"
        if suffix:
            window = f"{window}-{suffix}"
        return session, window

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
        if shutil.which("tmux") is None:
            raise RuntimeError("TmuxClaudeBackend requires the `tmux` binary on PATH")

        # Forked calls (e.g. parallel run_tests for unit/e2e/playwright on the
        # same tester role) need a unique tmux name + file paths, otherwise
        # the concurrent calls race for the same session and stomp each
        # other's stdout/.rc files.
        suffix = uuid.uuid4().hex[:6] if fork else ""
        if self.scope:
            session_name, window_name = self._scoped_names(suffix)
            file_stem = f"{session_name}-{window_name}"
        else:
            session_name = self._session_name(suffix)
            window_name = None
            file_stem = session_name
        workdir = cwd / ".tmux"
        workdir.mkdir(parents=True, exist_ok=True)
        out_path = workdir / f"{file_stem}.out"
        rc_path = workdir / f"{file_stem}.rc"
        prompt_path = workdir / f"{file_stem}.in"

        # Clear stale artifacts.
        for p in (out_path, rc_path):
            p.unlink(missing_ok=True)
        prompt_path.write_text(prompt)

        argv = _build_claude_argv(self.start_command, session_id, fork, model)
        # Pipeline: claude → tee (raw JSON to .out for the parser) →
        # stream_format (pretty terminal view in the tmux pane). The
        # `.out` file stays parseable; the human attaching to the pane
        # sees Claude-Code-TUI-style thinking/tool/result blocks.
        # PIPESTATUS[0] is claude's exit code, which is what we care
        # about; the formatter's exit code is ignored.
        formatter_cmd = (
            f"{shlex.quote(sys.executable)} -m prefect_orchestration.stream_format"
        )
        wrapper = (
            f"cd {shlex.quote(str(cwd))} && "
            f"{shlex.join(argv)} < {shlex.quote(str(prompt_path))} "
            f"2>&1 | tee {shlex.quote(str(out_path))} | {formatter_cmd}; "
            f"echo ${{PIPESTATUS[0]}} > {shlex.quote(str(rc_path))}"
        )

        try:
            target = _spawn_tmux(
                session_name=session_name,
                window_name=window_name,
                wrapper=wrapper,
                env=_clean_env(extra_env),
                cwd=cwd,
                geometry=(200, 50),
            )
            if self.attach_hint:
                if window_name:
                    print(
                        f"[tmux] attach with: tmux attach -t {session_name} "
                        f"\\; select-window -t {window_name}",
                        file=sys.stderr,
                        flush=True,
                    )
                else:
                    print(
                        f"[tmux] attach with: tmux attach -t {session_name}",
                        file=sys.stderr,
                        flush=True,
                    )

            _wait_for_rc(rc_path, target, timeout=self.timeout_s)
            rc = int(rc_path.read_text().strip() or "-1")
            stdout = out_path.read_text() if out_path.exists() else ""
            if rc != 0:
                raise RuntimeError(
                    f"claude CLI exited {rc}\nstdout tail: {stdout[-2000:]}"
                )
            result, new_sid = _parse_envelope(stdout, session_id)
        finally:
            _cleanup_tmux(target, scoped=window_name is not None)
            if prompt_path.exists():
                try:
                    prompt_path.unlink()
                except OSError:
                    pass

        return result, new_sid


def _stop_dir() -> Path:
    base = os.environ.get("PO_STOP_DIR")
    return Path(base) if base else Path.home() / ".cache" / "po-stops"


def _ensure_stop_hook(cwd: Path) -> None:
    """Lay down `<cwd>/.claude/settings.local.json` with our Stop hook.

    Writes to ``settings.local.json`` (NOT the committed ``settings.json``)
    because Claude Code merges both. The committed file is vulnerable to
    `git restore` / `git checkout HEAD --` invoked anywhere in the rig
    (agent cleanup, build steps, CI), which silently strips our Stop hook
    and wedges the whole run — the orchestrator polls `~/.cache/po-stops/`
    forever for a sentinel that never arrives. The local file is globally
    gitignored, so nothing in a normal git workflow can touch it.

    Idempotent and concurrency-safe — three roles spawning in parallel
    (e.g. lint + run_tests-unit + run_tests-e2e) used to race on
    read-modify-write and produce corrupt JSON, which claude then
    rejected, which hung the whole run. We:

      1. Use a fcntl-style lock on the settings file during read/write.
      2. Skip the write entirely if the file already has our Stop hook
         configured (the hook command is content-addressable).
      3. Write atomically via tmpfile + os.replace so a partial write
         never leaves bad JSON on disk for a peer to read.
    """
    import fcntl
    import os as _os
    import tempfile

    settings_dir = cwd / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings_path = settings_dir / "settings.local.json"
    lock_path = settings_dir / ".settings.lock"
    hook_cmd = f"{shlex.quote(sys.executable)} -m prefect_orchestration.stop_hook"
    desired_stop = [
        {"matcher": "", "hooks": [{"type": "command", "command": hook_cmd}]}
    ]

    with open(lock_path, "w") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        existing: dict[str, Any] = {}
        if settings_path.exists():
            try:
                existing = json.loads(settings_path.read_text()) or {}
            except json.JSONDecodeError:
                existing = {}
        if not isinstance(existing, dict):
            existing = {}
        hooks = existing.get("hooks") if isinstance(existing.get("hooks"), dict) else {}
        if not isinstance(hooks, dict):
            hooks = {}
        if hooks.get("Stop") == desired_stop:
            # Already configured; no write needed.
            return
        hooks["Stop"] = desired_stop
        existing["hooks"] = hooks

        # Atomic write: tmpfile in same dir, then replace.
        fd, tmp_path = tempfile.mkstemp(
            prefix=".settings-", suffix=".json", dir=settings_dir
        )
        try:
            with _os.fdopen(fd, "w") as f:
                f.write(json.dumps(existing, indent=2))
            _os.replace(tmp_path, settings_path)
        except Exception:
            try:
                _os.unlink(tmp_path)
            except OSError:
                pass
            raise


def _wait_for_tui_ready(
    session_name: str,
    *,
    fallback_s: float = 8.0,
    poll: float = 0.4,
) -> None:
    """Poll the tmux pane until the Claude Code TUI input box renders.

    The input row always contains a `❯` glyph; once it shows up the TUI is
    ready to accept paste. If we never see it within `fallback_s` (rare —
    splash screen issue, OAuth prompt, etc.) we proceed anyway and let
    the paste-buffer attempt surface whatever's wrong.
    """
    deadline = time.monotonic() + fallback_s
    while time.monotonic() < deadline:
        pane = subprocess.run(
            ["tmux", "capture-pane", "-pt", session_name],
            capture_output=True,
            check=False,
        )
        text = pane.stdout.decode(errors="replace")
        if "❯" in text or "Welcome back" in text:
            # TUI rendered. Tiny extra delay so the key handler is wired.
            time.sleep(0.2)
            return
        if "[claude exited" in text:
            # Caller's exit-marker check will pick this up.
            return
        time.sleep(poll)


def _wait_for_stop(
    sentinel: Path,
    session_name: str,
    *,
    timeout: float | None = None,
    poll: float = 0.4,
) -> None:
    """Block until the Stop hook writes our sentinel — or session disappears."""
    deadline = time.monotonic() + timeout if timeout is not None else None
    while deadline is None or time.monotonic() < deadline:
        if sentinel.exists() and sentinel.stat().st_size >= 0:
            return
        has = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True,
            check=False,
        )
        if has.returncode != 0 and not sentinel.exists():
            raise RuntimeError(
                f"tmux session {session_name!r} disappeared before Stop hook fired"
            )
        time.sleep(poll)
    raise TimeoutError(f"Stop hook for {session_name!r} did not fire within {timeout}s")


def _transcript_contains_prompt(jsonl_path: Path, marker: str) -> bool:
    """Return True if the JSONL transcript's user messages contain `marker`.

    Each line is a JSON event. We look at user-role messages and check if
    `marker` appears in the decoded content. This is escape-safe (literal
    newlines in `marker` match `\\n` sequences in the on-disk JSONL because
    `json.loads` decodes them back).
    """
    try:
        text = jsonl_path.read_text()
    except OSError:
        return False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = ev.get("message") if isinstance(ev, dict) else None
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            if marker in content:
                return True
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text_block = block.get("text") or block.get("content")
                    if isinstance(text_block, str) and marker in text_block:
                        return True
    return False


def _discover_resumed_sentinel(
    stop_dir: Path,
    cwd: Path,
    prompt: str,
    *,
    spawn_start: float,
    session_name: str,
    timeout: float | None = None,
    poll: float = 0.4,
) -> tuple[str, Path]:
    """Find the Stop sentinel for an agent spawned with `--resume <prior>`.

    Background (b9q): when claude is invoked with `--resume <prior>` it
    *does not* keep `<prior>` as the active session_id — it generates a
    new one. The Stop hook fires with the new id, so polling for
    `<prior>.stopped` waits forever even though the work is done.

    Discovery: scan `<stop_dir>/*.stopped` for entries that:
      1. were modified after `spawn_start` (this turn, not a prior one),
      2. carry our `cwd` (the rig path) — filters out other rigs,
      3. point at a transcript whose content contains the first slice of
         our prompt — this is the per-agent disambiguator since each
         role's prompt is unique.

    Returns `(session_id, transcript_path)`. Raises `RuntimeError` if
    the tmux session dies before any matching sentinel appears, or
    `TimeoutError` if `timeout` elapses with no match.
    """
    deadline = time.monotonic() + timeout if timeout is not None else None
    rig_str = str(cwd.resolve())
    # First 256 chars of the prompt is a robust per-spawn fingerprint —
    # PO prompts are role + iter + issue specific so collisions across
    # concurrent agents in the same rig are vanishingly unlikely. We
    # match against the *decoded* user message content (not raw JSONL
    # text) so escape sequences like \n in the prompt don't break the
    # comparison.
    prompt_marker = prompt[:256]
    while deadline is None or time.monotonic() < deadline:
        if stop_dir.exists():
            for sentinel in stop_dir.glob("*.stopped"):
                try:
                    if sentinel.stat().st_mtime < spawn_start:
                        continue
                    data = json.loads(sentinel.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                if data.get("cwd") != rig_str:
                    continue
                sid = data.get("session_id")
                transcript_path = data.get("transcript_path")
                if not sid or not transcript_path:
                    continue
                transcript = Path(transcript_path)
                if not transcript.exists():
                    continue
                if _transcript_contains_prompt(transcript, prompt_marker):
                    return sid, transcript
        # Liveness check — surface a tmux-dead error rather than spin
        # silently when claude exited before the hook could fire.
        has = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True,
            check=False,
        )
        if has.returncode != 0:
            raise RuntimeError(
                f"tmux session {session_name!r} disappeared before Stop hook "
                "fired (resume-discovery path)"
            )
        time.sleep(poll)
    raise TimeoutError(
        f"Stop hook for resumed session in {session_name!r} did not fire "
        f"within {timeout}s — check transcript at "
        f"~/.claude/projects/<rig-slug>/ for matching prompt."
    )


def _format_wedge_error(
    *,
    target: str,
    issue: str,
    role: str,
    session_id: str | None,
    timeout_s: float,
) -> str:
    """Build the diagnostic string for a wedged-agent RuntimeError (sav.1).

    Captures the last ~30 lines of the tmux pane so the operator can see
    rate-limit dialogs ('hit your limit'), missing-credentials errors,
    or whatever else stalled the session — instead of guessing why the
    flow stopped.
    """
    pane_tail = "(pane unavailable)"
    try:
        captured = subprocess.run(
            ["tmux", "capture-pane", "-pt", target, "-S", "-200"],
            capture_output=True,
            check=False,
        )
        text = captured.stdout.decode(errors="replace")
        if text:
            pane_tail = "\n".join(text.splitlines()[-30:])
    except (OSError, subprocess.SubprocessError):
        pass
    sid = session_id or "(unknown — pre-discovery)"
    return (
        f"agent session wedged: issue={issue!r} role={role!r} "
        f"session_id={sid} target={target!r} timeout={timeout_s}s. "
        f"Hint: check ~/.cache/po-stops/ for the missing sentinel and "
        f"the pane tail below for a rate-limit dialog "
        f"('hit your limit') or other failure.\n--- pane tail ---\n"
        f"{pane_tail}"
    )


def _last_assistant_text_from_jsonl(jsonl_path: Path) -> str:
    """Return the most recent assistant text-message from a Claude JSONL.

    Used by TmuxInteractiveClaudeBackend after the Stop hook fires to
    extract the agent's final reply (the orchestrator usually doesn't
    care — verdict files carry the truth — but we still return the text
    for logging / fallback.)
    """
    if not jsonl_path.exists():
        return ""
    last = ""
    try:
        for raw in jsonl_path.read_text().splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = ev.get("message") if isinstance(ev, dict) else None
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "assistant":
                continue
            for block in msg.get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text") or ""
                    if text.strip():
                        last = text
    except OSError:
        return last
    return last


@dataclass
class TmuxInteractiveClaudeBackend:
    """Run the **real** Claude Code TUI inside tmux.

    Unlike `TmuxClaudeBackend` (which uses `--print` and pipes
    stream-json through a formatter), this backend launches `claude`
    interactively — the same bordered TUI you get from `claude` in
    your normal terminal: input box, syntax-highlighted code blocks,
    scrollable chat. Attaching to the tmux session is a first-class
    lurking experience.

    Lifecycle:

    1. We generate (or reuse) a `session_id` and pass `--session-id`
       so the orchestrator knows the UUID without parsing stdout.
    2. We lay down a `<cwd>/.claude/settings.json` Stop hook (via
       `prefect_orchestration.stop_hook`) that touches a sentinel
       under `~/.cache/po-stops/<session_id>.stopped` when the agent
       finishes a turn.
    3. tmux session spawns `claude` interactively.
    4. Prompt is injected via `tmux load-buffer` + `paste-buffer -p`
       (bracketed paste) + `send-keys Enter`.
    5. Orchestrator polls for the sentinel; on appearance, kills the
       tmux session and returns. Result text is recovered from the
       per-session JSONL transcript.

    Verdict files (`$RUN_DIR/verdicts/<step>.json`) remain the source
    of truth — agents write them as part of their prompt. This backend
    is purely about how the role's *human-visible session* runs.
    """

    issue: str
    role: str
    start_command: str = "claude --dangerously-skip-permissions"
    attach_hint: bool = True
    # Finite default (sav.1) — see DEFAULT_AGENT_TIMEOUT_S. A wedged or
    # rate-limited Claude session would otherwise poll the sentinel
    # forever; with a finite deadline `run()` raises a diagnostic
    # RuntimeError naming the issue, role, session_id, and pane tail.
    timeout_s: float | None = DEFAULT_AGENT_TIMEOUT_S
    scope: str | None = None
    # Used as a fallback wait if `_wait_for_tui_ready` can't positively
    # detect the input prompt — typically only matters for very long
    # resumed conversations where claude takes longer to render.
    settle_s: float = 8.0

    def _session_name(self, suffix: str = "") -> str:
        from prefect_orchestration.attach import session_name as _session_name_for

        base = _session_name_for(self.issue, self.role)
        return f"{base}-{suffix}" if suffix else base

    def _scoped_names(self, suffix: str = "") -> tuple[str, str]:
        assert self.scope is not None
        safe_scope = self.scope.replace(".", "_")
        safe_issue = self.issue.replace(".", "_")
        safe_role = self.role.replace(".", "_")
        session = f"po-{safe_scope}"
        window = f"{safe_issue}-{safe_role}"
        if suffix:
            window = f"{window}-{suffix}"
        return session, window

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
        if shutil.which("tmux") is None:
            raise RuntimeError(
                "TmuxInteractiveClaudeBackend requires the `tmux` binary on PATH"
            )

        # Pick a session_id we can pass via --session-id. If we have a
        # prior valid UUID, resume it. Forks get a fresh id so concurrent
        # forked turns don't collide on the JSONL file.
        prior = session_id if session_id and _UUID_RE.match(session_id) else None

        suffix = uuid.uuid4().hex[:6] if fork else ""
        if self.scope:
            session_name, window_name = self._scoped_names(suffix)
            file_stem = f"{session_name}-{window_name}"
        else:
            session_name = self._session_name(suffix)
            window_name = None
            file_stem = session_name
        scoped = window_name is not None
        workdir = cwd / ".tmux"
        workdir.mkdir(parents=True, exist_ok=True)
        prompt_path = workdir / f"{file_stem}.in"
        prompt_path.write_text(prompt)

        _ensure_stop_hook(cwd)

        # Three modes for `claude` argv:
        #   * resume (prior, not fork): only `--resume <prior>`. Passing
        #     `--session-id` along with `--resume` makes claude bail with
        #     "session already exists" (and `--resume` does not preserve
        #     `<prior>` as the active session_id — claude generates a new
        #     one internally). We discover the new session_id post-hoc
        #     by matching the Stop sentinel's transcript content against
        #     our prompt — see `_discover_resumed_sentinel`. (b9q)
        #   * fork (resume + new sid): `--session-id <new> --resume <prior>
        #     --fork-session`. Branches are isolated by UUID; we know
        #     `<new>` upfront so the canonical sentinel poll works.
        #   * fresh: `--session-id <new>`. We pre-pick the UUID so we know
        #     where the JSONL transcript and Stop sentinel will land.
        if prior and not fork:
            # new_sid is unknown until claude creates it; discovery handles it.
            new_sid = None
            session_args = ["--resume", prior]
            sentinel = None
        elif fork and prior:
            new_sid = str(uuid.uuid4())
            session_args = [
                "--session-id",
                new_sid,
                "--resume",
                prior,
                "--fork-session",
            ]
            sentinel = _stop_dir() / f"{new_sid}.stopped"
            sentinel.unlink(missing_ok=True)
        else:
            new_sid = str(uuid.uuid4())
            session_args = ["--session-id", new_sid]
            sentinel = _stop_dir() / f"{new_sid}.stopped"
            sentinel.unlink(missing_ok=True)

        # Mark when we begin the spawn so `_discover_resumed_sentinel`
        # can filter out stale sentinel files from prior turns.
        spawn_start = time.time()

        argv = (
            shlex.split(self.start_command)
            + session_args
            + ["--model", model, "--setting-sources", "project,local"]
        )

        # Keep the tmux pane alive even if claude exits early (rate
        # limit, bad arg, missing UUID, etc.). Without the trailing
        # `; sleep infinity`, an early claude exit collapses bash, which
        # kills the tmux pane, which makes the next `paste-buffer`
        # fail with a useless "no current session" error and obscures
        # the real cause. With the keep-alive the pane stays around so
        # we can capture-pane it for diagnostics.
        wrapper = (
            f"cd {shlex.quote(str(cwd))} && "
            f"{shlex.join(argv)} ; "
            f'echo "[claude exited $? — session held open for diagnostics]" ; '
            f"sleep infinity"
        )
        target = _spawn_tmux(
            session_name=session_name,
            window_name=window_name,
            wrapper=wrapper,
            env=_clean_env(extra_env),
            cwd=cwd,
            geometry=(240, 60),
        )
        if self.attach_hint:
            if window_name:
                print(
                    f"[tmux] attach with: tmux attach -t {session_name} "
                    f"\\; select-window -t {window_name}",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                print(
                    f"[tmux] attach with: tmux attach -t {session_name}",
                    file=sys.stderr,
                    flush=True,
                )

        # Wait for the TUI to actually finish rendering its input box. The
        # bordered prompt always contains a `❯` glyph in the input row; if
        # we paste before that lands the keystrokes go to /dev/null. Falls
        # back to fixed `settle_s` after a hard cap so we never block
        # forever on a stuck splash screen.
        _wait_for_tui_ready(target, fallback_s=self.settle_s)

        # Verify the tmux target is still around AND claude actually
        # came up. If claude died on startup the pane will still
        # exist (sleep infinity keep-alive) but will contain the
        # "[claude exited ...]" marker — surface that as the error
        # so callers see the real reason instead of a cryptic
        # paste-buffer failure.
        has_target = subprocess.run(
            ["tmux", "has-session", "-t", target],
            capture_output=True,
            check=False,
        )
        if has_target.returncode != 0:
            raise RuntimeError(
                f"tmux target {target!r} disappeared before paste — "
                f"new-session/new-window likely failed silently. stderr: "
                f"{has_target.stderr.decode(errors='replace')}"
            )
        pane = subprocess.run(
            ["tmux", "capture-pane", "-pt", target, "-S", "-200"],
            capture_output=True,
            check=False,
        )
        pane_text = pane.stdout.decode(errors="replace")
        if "[claude exited" in pane_text:
            # Surface the last ~30 lines of pane output so the user can
            # see why claude exited (rate limit, bad flag, missing
            # credentials, etc.) instead of guessing.
            tail = "\n".join(pane_text.splitlines()[-30:])
            _cleanup_tmux(target, scoped=scoped)
            raise RuntimeError(
                f"claude exited before prompt could be injected (target "
                f"{target!r}). pane tail:\n{tail}"
            )

        # Inject prompt via bracketed paste so internal newlines stay
        # newlines (Shift+Enter equivalent), not "submit". Then send a
        # final Enter to submit.
        #
        # On large multiline prompts the Claude TUI shows the paste as a
        # `[Pasted text #N +K lines]` chip and needs a moment to commit
        # the bracketed-paste end-marker before Enter will trigger send.
        # If we send Enter too early (or the system is loaded) the chip
        # stays in the buffer and the agent sits idle indefinitely.
        # Verify-and-retry up to 3 times: send Enter, wait, check the
        # pane for an active-processing indicator, retry if not present.
        subprocess.run(
            ["tmux", "load-buffer", "-b", file_stem, str(prompt_path)],
            check=True,
        )
        subprocess.run(
            ["tmux", "paste-buffer", "-t", target, "-b", file_stem, "-p", "-d"],
            check=True,
        )

        # Indicators that submission landed and the agent is now working.
        # Any of these in the recent pane output means we're done nudging.
        active_markers = (
            "esc to interrupt",
            "tokens · esc",
            "Cogitated",
            "Composing",
            "Worked",
            "Crunched",
            "Whisking",
            "Julienning",
            "Thinking",
            "Skill(",
            "Bash(",
            "Searched",
            "hit your limit",  # rate-limit dialog — also a "submitted" state
        )
        for attempt in range(3):
            time.sleep(1.0 if attempt == 0 else 2.0)
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "Enter"],
                check=True,
            )
            time.sleep(1.5)
            pane = subprocess.run(
                ["tmux", "capture-pane", "-pt", target, "-S", "-50"],
                capture_output=True,
                check=False,
            ).stdout.decode(errors="replace")
            if any(m in pane for m in active_markers):
                break
            # Still sitting at the input prompt — likely the paste chip
            # never committed. Loop and try Enter again.

        # Rate-limit terminal-state guard (sav.2). The active_markers loop
        # treats 'hit your limit' as a "submission landed" signal because
        # the bracketed paste did in fact reach Claude — but Claude's
        # response is the terminal rate-limit dialog, not a real turn.
        # The Stop hook never fires for synthetic rate_limit events, so
        # `_wait_for_stop` would poll the sentinel until `timeout_s`
        # elapses (sav.1's finite timeout caps that, but the operator
        # still gets a useless "wedged" error 30 minutes later instead of
        # a clean "rate limit, try again at <reset>" right now).
        #
        # Two complementary detectors:
        #   1. Pane scrape — fast, works in fresh + resume + fork modes,
        #      surfaces the human-readable reset string Claude prints.
        #   2. JSONL synthetic-event scan — only viable in fresh/fork
        #      modes (resume doesn't know `new_sid` yet); deterministic
        #      ground-truth from Claude's own transcript writer.
        final_pane = subprocess.run(
            ["tmux", "capture-pane", "-pt", target, "-S", "-200"],
            capture_output=True,
            check=False,
        ).stdout.decode(errors="replace")
        reset = _detect_rate_limit_in_pane(final_pane)
        if reset is None and new_sid is not None:
            slug = str(cwd.resolve()).replace("/", "-")
            jsonl_probe = (
                Path.home() / ".claude" / "projects" / slug / f"{new_sid}.jsonl"
            )
            # The synthetic event lands a beat after the paste — poll
            # briefly so we don't false-negative on a fast capture.
            for _ in range(10):
                reset = _detect_rate_limit_in_jsonl(jsonl_probe)
                if reset is not None:
                    break
                time.sleep(0.3)
        if reset is not None:
            _cleanup_tmux(target, scoped=scoped)
            if sentinel is not None:
                sentinel.unlink(missing_ok=True)
            prompt_path.unlink(missing_ok=True)
            raise RateLimitError(reset_time=reset or None)

        try:
            if new_sid is None:
                # Resume mode: claude assigned its own session_id internally
                # — find it via prompt-content match against the sentinel's
                # transcript_path.
                try:
                    new_sid, jsonl = _discover_resumed_sentinel(
                        _stop_dir(),
                        cwd,
                        prompt,
                        spawn_start=spawn_start,
                        session_name=target,
                        timeout=self.timeout_s,
                    )
                except TimeoutError as exc:
                    raise RuntimeError(
                        _format_wedge_error(
                            target=target,
                            issue=self.issue,
                            role=self.role,
                            session_id=None,
                            timeout_s=self.timeout_s or 0.0,
                        )
                    ) from exc
                sentinel = _stop_dir() / f"{new_sid}.stopped"
            else:
                try:
                    _wait_for_stop(sentinel, target, timeout=self.timeout_s)
                except TimeoutError as exc:
                    raise RuntimeError(
                        _format_wedge_error(
                            target=target,
                            issue=self.issue,
                            role=self.role,
                            session_id=new_sid,
                            timeout_s=self.timeout_s or 0.0,
                        )
                    ) from exc
                slug = str(cwd.resolve()).replace("/", "-")
                jsonl = Path.home() / ".claude" / "projects" / slug / f"{new_sid}.jsonl"
            # Pull the agent's final assistant text from the JSONL transcript
            # (verdict files carry orchestrator-readable truth; this is for logs).
            result = (
                _last_assistant_text_from_jsonl(jsonl) or "[interactive turn complete]"
            )
        finally:
            _cleanup_tmux(target, scoped=scoped)
            if sentinel is not None:
                sentinel.unlink(missing_ok=True)
            prompt_path.unlink(missing_ok=True)

        return result, new_sid


def _render_with_inbox(mails: list[Any], prompt_text: str) -> str:
    """Prepend a `<mail-inbox>` block to the prompt; passthrough if empty."""
    if not mails:
        return prompt_text
    parts: list[str] = []
    for i, m in enumerate(mails):
        if i > 0:
            parts.append("---")
        ts = getattr(m, "created_at", None)
        ts_str = (
            ts.isoformat() if hasattr(ts, "isoformat") else (str(ts) if ts else "?")
        )
        from_agent = getattr(m, "from_agent", None) or "?"
        subject = getattr(m, "subject", "") or ""
        body = getattr(m, "body", "") or ""
        parts.append(f"[{ts_str} | from={from_agent}] subject: {subject}")
        if body:
            parts.append(body)
    block = "<mail-inbox>\n" + "\n".join(parts) + "\n</mail-inbox>"
    return f"{block}\n\n{prompt_text}"


@dataclass
class AgentSession:
    """One logical agent (a role) with a persistent Claude session.

    Sessions are keyed by (role, issue_id) — that's the unit we want to
    resume across flow runs. The orchestrator persists `session_id` in
    beads metadata so a crashed flow can pick up the same context.
    """

    role: str
    repo_path: Path
    backend: SessionBackend = field(default_factory=ClaudeCliBackend)
    session_id: str | None = None
    model: str = "opus"
    # Optional pack-supplied hooks for auto-injecting unread mail.
    # `mail_fetcher(role) -> list[Mail-like]`; objects need .id/.subject/.body
    # and may have .from_agent and .created_at. `mail_marker(mail_id)` closes.
    # Keeping these as injected callables avoids importing pack modules from
    # core (po-formulas is a sibling package; core must work without it).
    mail_fetcher: Callable[[str], list[Any]] | None = None
    mail_marker: Callable[[str], None] | None = None
    skip_mail_inject: bool = False
    overlay: bool = True
    skills: bool = True
    # Per-agent secret injection. When set, role-scoped env vars
    # (e.g. `SLACK_TOKEN_<ROLE>`) are re-keyed (`SLACK_TOKEN`) and
    # passed to the backend as `extra_env`. Backends scrub peer-role
    # scoped vars from base env before overlay, so role A can't see
    # role B's secrets.
    secret_provider: SecretProvider | None = None
    # Optional issue id for telemetry attribution. Callers (the software-dev
    # pack flows) typically know the bead id and pass it through; left None
    # for in-tree tests / standalone use.
    issue_id: str | None = None
    _materialized: bool = field(default=False, init=False, repr=False)
    _turn_index: int = field(default=0, init=False, repr=False)

    def _materialize_packs_once(self) -> None:
        """Lazily copy pack overlay + skills into the rig cwd before the first turn."""
        if self._materialized:
            return
        self._materialized = True
        if not self.overlay and not self.skills:
            return
        # Imported lazily to keep AgentSession construction cheap and
        # avoid loading importlib.metadata in tests that stub the backend.
        from prefect_orchestration.pack_overlay import materialize_packs

        try:
            materialize_packs(
                self.repo_path,
                role=self.role,
                overlay=self.overlay,
                skills=self.skills,
            )
        except Exception:
            logger.exception(
                "pack overlay/skills materialization failed for role=%s cwd=%s",
                self.role,
                self.repo_path,
            )

    def prompt(
        self,
        text: str,
        *,
        fork: bool = False,
        expect_verdict: Path | None = None,
    ) -> str:
        """Send a prompt; updates `session_id` in place.

        Prepends an `<mail-inbox>` block listing any unread mail addressed
        to this role (via `mail_fetcher`). On successful turn return,
        marks those messages read (via `mail_marker`). On exception,
        leaves them unread so the next turn re-renders them.

        ``expect_verdict``: when set, after the turn returns the path is
        checked. If missing, a single nudge turn is fired (reusing the
        post-turn ``session_id``, no fork, no mail re-injection) telling
        the agent to write the file. Capped at one retry — if the file
        is still absent, fall through and let the caller's
        ``read_verdict`` raise the loud ``FileNotFoundError``.
        """
        self._materialize_packs_once()
        mails = self._fetch_inbox()
        full_text = _render_with_inbox(mails, text)

        extra_env = (
            self.secret_provider.get_role_env(self.role)
            if self.secret_provider is not None
            else None
        )

        from prefect_orchestration import telemetry

        tel = telemetry.select_backend()
        self._turn_index += 1
        attrs: dict[str, Any] = {
            "role": self.role,
            "issue_id": self.issue_id,
            "session_id": self.session_id,
            "turn_index": self._turn_index,
            "fork_session": fork,
            "model": self.model,
        }
        tmux_name = self._tmux_session_name(fork=fork)
        if tmux_name:
            attrs["tmux_session"] = tmux_name

        with tel.span("agent.prompt", **attrs) as span:
            try:
                result, new_sid = self.backend.run(
                    full_text,
                    session_id=self.session_id,
                    cwd=self.repo_path,
                    fork=fork,
                    model=self.model,
                    extra_env=extra_env,
                )
            except BaseException as e:
                span.record_exception(e)
                span.set_status("ERROR", f"{type(e).__name__}: {e}")
                raise
            span.set_attribute("new_session_id", new_sid)
        self.session_id = new_sid
        self._mark_read(mails)

        if expect_verdict is not None and not expect_verdict.exists():
            result = self._nudge_for_verdict(
                expect_verdict, extra_env=extra_env, tel=tel, prior_result=result
            )
        return result

    def _nudge_for_verdict(
        self,
        verdict_path: Path,
        *,
        extra_env: Mapping[str, str] | None,
        tel: Any,
        prior_result: str,
    ) -> str:
        """Fire one nudge turn re-prompting the agent to emit the verdict file.

        Reuses the current ``session_id`` so the agent sees its prior
        reasoning. Skips mail-injection (this is a forced internal retry,
        not a fresh turn). One retry only — no recursion.
        """
        nudge = (
            f"You ended your previous turn without writing the required verdict "
            f"file at {verdict_path}. Write it now using the JSON shape your "
            f"role prompt specified, then stop. Do not redo the analysis — "
            f"only emit the file."
        )
        self._turn_index += 1
        attrs: dict[str, Any] = {
            "role": self.role,
            "issue_id": self.issue_id,
            "session_id": self.session_id,
            "turn_index": self._turn_index,
            "fork_session": False,
            "model": self.model,
            "nudge": True,
            "verdict_path": str(verdict_path),
        }
        with tel.span("agent.prompt.nudge", **attrs) as span:
            try:
                result, new_sid = self.backend.run(
                    nudge,
                    session_id=self.session_id,
                    cwd=self.repo_path,
                    fork=False,
                    model=self.model,
                    extra_env=extra_env,
                )
            except BaseException as e:
                span.record_exception(e)
                span.set_status("ERROR", f"{type(e).__name__}: {e}")
                raise
            span.set_attribute("new_session_id", new_sid)
        self.session_id = new_sid
        return result or prior_result

    def _tmux_session_name(self, *, fork: bool) -> str | None:
        """Best-effort lookup of the tmux session name for telemetry.

        Forked turns randomise a 6-char suffix at backend.run time, so
        we can't predict the name; only emit for non-fork tmux runs.
        """
        if fork:
            return None
        get_name = getattr(self.backend, "_session_name", None)
        if not callable(get_name):
            return None
        try:
            return get_name()
        except Exception:
            return None

    def _fetch_inbox(self) -> list[Any]:
        if self.skip_mail_inject or self.mail_fetcher is None:
            return []
        try:
            mails = list(self.mail_fetcher(self.role) or [])
        except Exception:
            logger.exception(
                "mail_fetcher failed for role %r; skipping inject", self.role
            )
            return []
        if len(mails) > MAX_INBOX_MESSAGES:
            # Keep the most recent N. Sort defensively; created_at may be None.
            mails.sort(
                key=lambda m: getattr(m, "created_at", None) or "",
                reverse=True,
            )
            mails = mails[:MAX_INBOX_MESSAGES]
        return mails

    def _mark_read(self, mails: list[Any]) -> None:
        if not mails or self.mail_marker is None:
            return
        for m in mails:
            mail_id = getattr(m, "id", None)
            if not mail_id:
                continue
            try:
                self.mail_marker(str(mail_id))
            except Exception:
                logger.exception("mail_marker failed for id %r", mail_id)

    def fork(self) -> AgentSession:
        """Return a child session that shares prior context but branches off.

        Used when a critic wants to explore an alternative without
        polluting the main review thread.
        """
        if not self.session_id:
            raise ValueError("cannot fork a session that hasn't run yet")
        child = AgentSession(
            role=self.role,
            repo_path=self.repo_path,
            backend=self.backend,
            session_id=self.session_id,
            model=self.model,
            secret_provider=self.secret_provider,
            issue_id=self.issue_id,
        )
        # Mark the next .prompt() call as a fork via a sentinel prompt
        # — callers should use `child.prompt(text, fork=True)` explicitly
        # for the first turn. We don't auto-fork here to keep the API
        # predictable.
        return child
