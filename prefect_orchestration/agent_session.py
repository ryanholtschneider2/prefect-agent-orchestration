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
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)

MAX_INBOX_MESSAGES = 20

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class SessionBackend(Protocol):
    """A backend that can run a prompt in a session and return output + new session_id."""

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None,
        cwd: Path,
        fork: bool = False,
        model: str = "opus",
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
    ) -> tuple[str, str]:
        cmd = _build_claude_argv(self.start_command, session_id, fork, model)

        proc = subprocess.run(
            cmd,
            input=prompt,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI exited {proc.returncode}\nstderr: {proc.stderr[:2000]}"
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

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None,
        cwd: Path,
        fork: bool = False,
        model: str = "opus",
    ) -> tuple[str, str]:
        import re as _re

        sid = session_id or f"stub-{uuid.uuid4().hex[:8]}"
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


def _clean_env() -> dict[str, str]:
    """Env for Claude CLI subprocesses.

    Strip ANTHROPIC_API_KEY so the CLI uses OAuth (the user's subscription)
    rather than the key — matches the user-global convention.
    """
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
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


@dataclass
class TmuxClaudeBackend:
    """Spawn `claude --print` inside a detached tmux session.

    Lets a human `tmux attach -t po-{issue}-{role}` mid-run to watch the
    agent's live output. Preserves the `ClaudeCliBackend` contract:
    same argv, same JSON-envelope parsing, verdict files land in
    `cwd/verdicts/` exactly as before.

    Session naming: `po-{issue}-{role}`. Pre-existing sessions with the
    same name are killed and replaced (prior run probably crashed).
    """

    issue: str
    role: str
    start_command: str = "claude --dangerously-skip-permissions"
    attach_hint: bool = True
    timeout_s: float | None = None

    def _session_name(self, suffix: str = "") -> str:
        # tmux uses '.' as a pane separator in target specs (session.window.pane).
        # Replace dots in issue IDs like `prefect-orchestration-4ja.1` so
        # `kill-session -t <name>` and `send-keys -t <name>` resolve to the
        # whole session, not a pane inside it.
        safe_issue = self.issue.replace(".", "_")
        safe_role = self.role.replace(".", "_")
        base = f"po-{safe_issue}-{safe_role}"
        return f"{base}-{suffix}" if suffix else base

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None,
        cwd: Path,
        fork: bool = False,
        model: str = "opus",
    ) -> tuple[str, str]:
        if shutil.which("tmux") is None:
            raise RuntimeError("TmuxClaudeBackend requires the `tmux` binary on PATH")

        # Forked calls (e.g. parallel run_tests for unit/e2e/playwright on the
        # same tester role) need a unique tmux name + file paths, otherwise
        # the concurrent calls race for the same session and stomp each
        # other's stdout/.rc files.
        suffix = uuid.uuid4().hex[:6] if fork else ""
        name = self._session_name(suffix)
        workdir = cwd / ".tmux"
        workdir.mkdir(parents=True, exist_ok=True)
        out_path = workdir / f"{name}.out"
        rc_path = workdir / f"{name}.rc"
        prompt_path = workdir / f"{name}.in"

        # Clear stale artifacts and any session with the same name.
        for p in (out_path, rc_path):
            p.unlink(missing_ok=True)
        prompt_path.write_text(prompt)

        kill = subprocess.run(
            ["tmux", "kill-session", "-t", name],
            capture_output=True,
            check=False,
        )
        if kill.returncode == 0:
            print(
                f"[tmux] killed pre-existing session {name!r}",
                file=sys.stderr,
                flush=True,
            )

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
            subprocess.run(
                [
                    "tmux",
                    "new-session",
                    "-d",
                    "-s",
                    name,
                    "-x",
                    "200",
                    "-y",
                    "50",
                    "bash",
                    "-lc",
                    wrapper,
                ],
                check=True,
                env=_clean_env(),
                cwd=cwd,
            )
            if self.attach_hint:
                print(
                    f"[tmux] attach with: tmux attach -t {name}",
                    file=sys.stderr,
                    flush=True,
                )

            _wait_for_rc(rc_path, name, timeout=self.timeout_s)
            rc = int(rc_path.read_text().strip() or "-1")
            stdout = out_path.read_text() if out_path.exists() else ""
            if rc != 0:
                raise RuntimeError(
                    f"claude CLI exited {rc}\nstdout tail: {stdout[-2000:]}"
                )
            result, new_sid = _parse_envelope(stdout, session_id)
        finally:
            subprocess.run(
                ["tmux", "kill-session", "-t", name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
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
    """Lay down `<cwd>/.claude/settings.json` with our Stop hook.

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
    settings_path = settings_dir / "settings.json"
    lock_path = settings_dir / ".settings.lock"
    hook_cmd = f"{shlex.quote(sys.executable)} -m prefect_orchestration.stop_hook"
    desired_stop = [{"matcher": "", "hooks": [{"type": "command", "command": hook_cmd}]}]

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
        fd, tmp_path = tempfile.mkstemp(prefix=".settings-", suffix=".json", dir=settings_dir)
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
    raise TimeoutError(
        f"Stop hook for {session_name!r} did not fire within {timeout}s"
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
    timeout_s: float | None = None
    # Used as a fallback wait if `_wait_for_tui_ready` can't positively
    # detect the input prompt — typically only matters for very long
    # resumed conversations where claude takes longer to render.
    settle_s: float = 8.0

    def _session_name(self, suffix: str = "") -> str:
        safe_issue = self.issue.replace(".", "_")
        safe_role = self.role.replace(".", "_")
        base = f"po-{safe_issue}-{safe_role}"
        return f"{base}-{suffix}" if suffix else base

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None,
        cwd: Path,
        fork: bool = False,
        model: str = "opus",
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
        name = self._session_name(suffix)
        workdir = cwd / ".tmux"
        workdir.mkdir(parents=True, exist_ok=True)
        prompt_path = workdir / f"{name}.in"
        prompt_path.write_text(prompt)

        _ensure_stop_hook(cwd)

        # Three modes for `claude` argv:
        #   * resume (prior, not fork): only `--resume <prior>`. Passing
        #     `--session-id` along with `--resume` makes claude bail with
        #     "session already exists", which is the bug that wedged
        #     prefect-orchestration-tyf.1's first triage. Reused UUID is
        #     known to caller (== prior).
        #   * fork (resume + new sid): `--session-id <new> --resume <prior>
        #     --fork-session`. Branches are isolated by UUID.
        #   * fresh: `--session-id <new>`. We pre-pick the UUID so we know
        #     where the JSONL transcript and Stop sentinel will land.
        if prior and not fork:
            new_sid = prior
            session_args = ["--resume", prior]
        elif fork and prior:
            new_sid = str(uuid.uuid4())
            session_args = ["--session-id", new_sid, "--resume", prior, "--fork-session"]
        else:
            new_sid = str(uuid.uuid4())
            session_args = ["--session-id", new_sid]

        sentinel = _stop_dir() / f"{new_sid}.stopped"
        sentinel.unlink(missing_ok=True)

        # Kill any prior session with the same name.
        subprocess.run(
            ["tmux", "kill-session", "-t", name],
            capture_output=True,
            check=False,
        )

        argv = shlex.split(self.start_command) + session_args + ["--model", model]

        # Keep the tmux session alive even if claude exits early (rate
        # limit, bad arg, missing UUID, etc.). Without the trailing
        # `; sleep infinity`, an early claude exit collapses bash, which
        # kills the tmux session, which makes the next `paste-buffer`
        # fail with a useless "no current session" error and obscures
        # the real cause. With the keep-alive the pane stays around so
        # we can capture-pane it for diagnostics.
        wrapper = (
            f"cd {shlex.quote(str(cwd))} && "
            f"{shlex.join(argv)} ; "
            f"echo \"[claude exited $? — session held open for diagnostics]\" ; "
            f"sleep infinity"
        )
        subprocess.run(
            [
                "tmux", "new-session", "-d", "-s", name,
                "-x", "240", "-y", "60",
                "bash", "-lc", wrapper,
            ],
            check=True,
            env=_clean_env(),
            cwd=cwd,
        )
        if self.attach_hint:
            print(
                f"[tmux] attach with: tmux attach -t {name}",
                file=sys.stderr,
                flush=True,
            )

        # Wait for the TUI to actually finish rendering its input box. The
        # bordered prompt always contains a `❯` glyph in the input row; if
        # we paste before that lands the keystrokes go to /dev/null. Falls
        # back to fixed `settle_s` after a hard cap so we never block
        # forever on a stuck splash screen.
        _wait_for_tui_ready(name, fallback_s=self.settle_s)

        # Verify the tmux session is still around AND claude actually
        # came up. If claude died on startup the session will still
        # exist (sleep infinity keep-alive) but the pane will contain
        # the "[claude exited ...]" marker — surface that as the error
        # so callers see the real reason instead of a cryptic
        # paste-buffer failure.
        has_session = subprocess.run(
            ["tmux", "has-session", "-t", name],
            capture_output=True,
            check=False,
        )
        if has_session.returncode != 0:
            raise RuntimeError(
                f"tmux session {name!r} disappeared before paste — "
                f"new-session likely failed silently. stderr: "
                f"{has_session.stderr.decode(errors='replace')}"
            )
        pane = subprocess.run(
            ["tmux", "capture-pane", "-pt", name, "-S", "-200"],
            capture_output=True,
            check=False,
        )
        pane_text = pane.stdout.decode(errors="replace")
        if "[claude exited" in pane_text:
            # Surface the last ~30 lines of pane output so the user can
            # see why claude exited (rate limit, bad flag, missing
            # credentials, etc.) instead of guessing.
            tail = "\n".join(pane_text.splitlines()[-30:])
            subprocess.run(
                ["tmux", "kill-session", "-t", name],
                capture_output=True,
                check=False,
            )
            raise RuntimeError(
                f"claude exited before prompt could be injected (session "
                f"{name!r}). pane tail:\n{tail}"
            )

        # Inject prompt via bracketed paste so internal newlines stay
        # newlines (Shift+Enter equivalent), not "submit". Then send a
        # final Enter to submit.
        subprocess.run(
            ["tmux", "load-buffer", "-b", name, str(prompt_path)],
            check=True,
        )
        subprocess.run(
            ["tmux", "paste-buffer", "-t", name, "-b", name, "-p", "-d"],
            check=True,
        )
        time.sleep(0.3)
        subprocess.run(
            ["tmux", "send-keys", "-t", name, "Enter"],
            check=True,
        )

        try:
            _wait_for_stop(sentinel, name, timeout=self.timeout_s)
            # Pull the agent's final assistant text from the JSONL transcript
            # (verdict files carry orchestrator-readable truth; this is for logs).
            slug = str(cwd.resolve()).replace("/", "-")
            jsonl = Path.home() / ".claude" / "projects" / slug / f"{new_sid}.jsonl"
            result = _last_assistant_text_from_jsonl(jsonl) or "[interactive turn complete]"
        finally:
            subprocess.run(
                ["tmux", "kill-session", "-t", name],
                capture_output=True,
                check=False,
            )
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
    _materialized: bool = field(default=False, init=False, repr=False)

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

    def prompt(self, text: str, *, fork: bool = False) -> str:
        """Send a prompt; updates `session_id` in place.

        Prepends an `<mail-inbox>` block listing any unread mail addressed
        to this role (via `mail_fetcher`). On successful turn return,
        marks those messages read (via `mail_marker`). On exception,
        leaves them unread so the next turn re-renders them.
        """
        self._materialize_packs_once()
        mails = self._fetch_inbox()
        full_text = _render_with_inbox(mails, text)

        result, new_sid = self.backend.run(
            full_text,
            session_id=self.session_id,
            cwd=self.repo_path,
            fork=fork,
            model=self.model,
        )
        self.session_id = new_sid
        self._mark_read(mails)
        return result

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
        )
        # Mark the next .prompt() call as a fork via a sentinel prompt
        # — callers should use `child.prompt(text, fork=True)` explicitly
        # for the first turn. We don't auto-fork here to keep the API
        # predictable.
        return child
