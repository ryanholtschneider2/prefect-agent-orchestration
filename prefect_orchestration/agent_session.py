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
from typing import Protocol

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
    """Construct the `claude --print ...` argv shared by all backends."""
    argv = shlex.split(start_command) + [
        "--print",
        "--output-format",
        "json",
        "--model",
        model,
    ]
    if session_id and _UUID_RE.match(session_id):
        argv += ["--resume", session_id]
        if fork:
            argv.append("--fork-session")
    return argv


def _parse_envelope(stdout: str, prior_sid: str | None) -> tuple[str, str]:
    """Mirror of ClaudeCliBackend's envelope handling."""
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout, prior_sid or str(uuid.uuid4())
    new_sid = envelope.get("session_id") or prior_sid or str(uuid.uuid4())
    result = envelope.get("result", stdout)
    return result, new_sid


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

    def _session_name(self) -> str:
        # tmux uses '.' as a pane separator in target specs (session.window.pane).
        # Replace dots in issue IDs like `prefect-orchestration-4ja.1` so
        # `kill-session -t <name>` and `send-keys -t <name>` resolve to the
        # whole session, not a pane inside it.
        safe_issue = self.issue.replace(".", "_")
        safe_role = self.role.replace(".", "_")
        return f"po-{safe_issue}-{safe_role}"

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

        name = self._session_name()
        workdir = cwd / ".tmux"
        workdir.mkdir(parents=True, exist_ok=True)
        out_path = workdir / f"{name}.out"
        rc_path = workdir / f"{name}.rc"
        prompt_path = workdir / f"{name}.in"

        # Clear stale artifacts and any session with the same name.
        for p in (out_path, rc_path):
            if p.exists():
                p.unlink()
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
        wrapper = (
            f"cd {shlex.quote(str(cwd))} && "
            f"{shlex.join(argv)} < {shlex.quote(str(prompt_path))} "
            f"2>&1 | tee {shlex.quote(str(out_path))}; "
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

    def prompt(self, text: str, *, fork: bool = False) -> str:
        """Send a prompt; updates `session_id` in place."""
        result, new_sid = self.backend.run(
            text,
            session_id=self.session_id,
            cwd=self.repo_path,
            fork=fork,
            model=self.model,
        )
        self.session_id = new_sid
        return result

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
