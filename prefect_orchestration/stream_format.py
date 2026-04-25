"""Pretty-print Claude `--output-format stream-json` for human lurking.

Reads JSON-per-line events from stdin, emits a compact terminal-friendly
view to stdout. The raw stream stays available via the tmux backend's
`.out` file (which is what the orchestrator parses for the result
envelope) — this module is *only* for what shows up inside the tmux
pane when a human attaches.

Run as: `python -m prefect_orchestration.stream_format` (stdin → stdout).
"""

from __future__ import annotations

import json
import sys
from typing import Any

DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
GREY = "\033[90m"


def _truncate(text: str, n: int = 200) -> str:
    s = " ".join(text.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _fmt_tool_input(inp: Any, n: int = 160) -> str:
    if isinstance(inp, dict):
        for key in ("command", "file_path", "pattern", "url", "prompt", "query"):
            if key in inp and isinstance(inp[key], str):
                return _truncate(inp[key], n)
        return _truncate(json.dumps(inp), n)
    return _truncate(str(inp), n)


def _print(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def handle(event: dict[str, Any]) -> None:
    etype = event.get("type")
    if etype == "system" and event.get("subtype") == "init":
        sid = event.get("session_id", "?")
        model = event.get("model", "?")
        _print(f"{GREY}─── session {sid[:8]}… · {model} ───{RESET}")
        return
    if etype == "assistant":
        for block in event.get("message", {}).get("content", []) or []:
            btype = block.get("type")
            if btype == "thinking":
                _print(f"{DIM}💭 {_truncate(block.get('thinking', ''), 400)}{RESET}")
            elif btype == "text":
                text = block.get("text", "").strip()
                if text:
                    _print(f"{BOLD}🤖 {text}{RESET}")
            elif btype == "tool_use":
                name = block.get("name", "?")
                arg = _fmt_tool_input(block.get("input"))
                _print(f"{CYAN}🔧 {name}{RESET}  {GREY}{arg}{RESET}")
        return
    if etype == "user":
        for block in event.get("message", {}).get("content", []) or []:
            if block.get("type") == "tool_result":
                content = block.get("content", "")
                if isinstance(content, list):
                    parts = []
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            parts.append(c.get("text", ""))
                    content = "\n".join(parts)
                ok = not block.get("is_error")
                glyph = f"{GREEN}↳{RESET}" if ok else f"{RED}✗{RESET}"
                _print(f"  {glyph} {GREY}{_truncate(str(content), 280)}{RESET}")
        return
    if etype == "result":
        dur_ms = event.get("duration_ms", 0)
        cost = event.get("total_cost_usd")
        is_err = event.get("is_error")
        result = _truncate(str(event.get("result", "")), 600)
        glyph = f"{RED}✗ result{RESET}" if is_err else f"{GREEN}✓ result{RESET}"
        cost_s = f" · ${cost:.4f}" if isinstance(cost, (int, float)) else ""
        _print(f"{glyph} ({dur_ms}ms{cost_s})")
        if result:
            _print(f"{result}")
        return
    if etype == "rate_limit_event":
        info = event.get("rate_limit_info", {})
        status = info.get("status", "?")
        if status != "allowed":
            _print(f"{YELLOW}⚠ rate limit: {status}{RESET}")
        return
    # Other event types (stream_event, partial_message, etc.) — silent.


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            _print(f"{GREY}{_truncate(line, 200)}{RESET}")
            continue
        if isinstance(event, dict):
            try:
                handle(event)
            except Exception as exc:  # never break the lurker
                _print(f"{RED}[stream_format error: {exc}]{RESET}")


if __name__ == "__main__":
    main()
