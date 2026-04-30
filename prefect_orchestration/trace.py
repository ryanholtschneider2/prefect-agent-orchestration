"""Parse + format Claude Code agent traces from JSONL session files.

JSONL files live at ~/.claude/projects/<slug>/<uuid>.jsonl where
slug = rig_path with all '/' replaced by '-'.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def _project_slug(rig_path: Path) -> str:
    return str(rig_path).replace("/", "-")


def find_jsonl(uuid: str, rig_path: Path) -> Path | None:
    """Locate <uuid>.jsonl under ~/.claude/projects/<slug>/. Fallback: scan all slugs."""
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.is_dir():
        return None
    candidate = projects_dir / _project_slug(rig_path) / f"{uuid}.jsonl"
    if candidate.exists():
        return candidate
    for f in projects_dir.glob(f"*/{uuid}.jsonl"):
        return f
    return None


@dataclass
class TurnRecord:
    index: int
    timestamp: str
    thinking: bool
    tool_names: list[str]
    tool_inputs_preview: list[str]
    text_preview: str
    in_tok: int
    out_tok: int
    cache_r: int
    cache_w: int
    model: str
    wall_s: float


@dataclass
class RoleTrace:
    role: str
    uuid: str
    turns: list[TurnRecord]
    jsonl_path: Path | None


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def parse_jsonl(path: Path) -> list[TurnRecord]:
    """Read a JSONL file, return one TurnRecord per assistant message."""
    turns: list[TurnRecord] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message") or {}
            usage = msg.get("usage") or {}
            content = msg.get("content") or []
            model = msg.get("model") or "unknown"
            in_tok = usage.get("input_tokens", 0)
            out_tok = usage.get("output_tokens", 0)
            cache_r = usage.get("cache_read_input_tokens", 0)
            cache_w = usage.get("cache_creation_input_tokens", 0)
            thinking = any(c.get("type") == "thinking" for c in content)
            tool_names = [c["name"] for c in content if c.get("type") == "tool_use"]
            tool_inputs_preview = [
                repr(c.get("input", {}))[:80] for c in content if c.get("type") == "tool_use"
            ]
            text_preview = ""
            for c in content:
                if c.get("type") == "text":
                    text_preview = (c.get("text") or "")[:120]
                    break
            ts = rec.get("timestamp", "")
            if turns:
                first_ts = _parse_ts(turns[0].timestamp)
                try:
                    wall_s = (_parse_ts(ts) - first_ts).total_seconds()
                except Exception:
                    wall_s = 0.0
            else:
                wall_s = 0.0
            turns.append(
                TurnRecord(
                    index=len(turns) + 1,
                    timestamp=ts,
                    thinking=thinking,
                    tool_names=tool_names,
                    tool_inputs_preview=tool_inputs_preview,
                    text_preview=text_preview,
                    in_tok=in_tok,
                    out_tok=out_tok,
                    cache_r=cache_r,
                    cache_w=cache_w,
                    model=model,
                    wall_s=wall_s,
                )
            )
    return turns


@dataclass
class RoleSummary:
    role: str
    model: str
    turns: int
    tools: int
    in_tok: int
    out_tok: int
    cache_r: int
    think_turns: int
    wall_s: float


def summarize(traces: list[RoleTrace]) -> list[RoleSummary]:
    summaries: list[RoleSummary] = []
    for rt in traces:
        if not rt.turns:
            summaries.append(
                RoleSummary(
                    role=rt.role,
                    model="unknown",
                    turns=0,
                    tools=0,
                    in_tok=0,
                    out_tok=0,
                    cache_r=0,
                    think_turns=0,
                    wall_s=0.0,
                )
            )
            continue
        model = rt.turns[-1].model
        for t in rt.turns:
            if t.model != "unknown":
                model = t.model
                break
        summaries.append(
            RoleSummary(
                role=rt.role,
                model=model,
                turns=len(rt.turns),
                tools=sum(len(t.tool_names) for t in rt.turns),
                in_tok=sum(t.in_tok for t in rt.turns),
                out_tok=sum(t.out_tok for t in rt.turns),
                cache_r=sum(t.cache_r for t in rt.turns),
                think_turns=sum(1 for t in rt.turns if t.thinking),
                wall_s=rt.turns[-1].wall_s if rt.turns else 0.0,
            )
        )
    return summaries


def _fmt_wall(s: float) -> str:
    m, sec = divmod(int(s), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def format_summary_table(summaries: list[RoleSummary]) -> str:
    headers = ("ROLE", "MODEL", "TURNS", "TOOLS", "IN_TOK", "OUT_TOK", "CACHE_R", "THINK", "WALL")
    data = [
        (
            s.role,
            s.model,
            str(s.turns),
            str(s.tools),
            str(s.in_tok),
            str(s.out_tok),
            str(s.cache_r),
            str(s.think_turns),
            _fmt_wall(s.wall_s),
        )
        for s in summaries
    ]
    widths = [
        max(len(h), *(len(row[i]) for row in data)) if data else len(h)
        for i, h in enumerate(headers)
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
    lines.extend(fmt.format(*row) for row in data)
    return "\n".join(lines)


def format_transcript(traces: list[RoleTrace], role: str) -> str:
    rt = next((t for t in traces if t.role == role), None)
    if rt is None:
        return f"no trace found for role {role!r}"
    if not rt.turns:
        return f"no turns recorded for role {role!r} (uuid={rt.uuid})"
    chunks: list[str] = []
    for turn in rt.turns:
        wall_fmt = _fmt_wall(turn.wall_s)
        flags = []
        if turn.thinking:
            flags.append("thinking")
        if turn.tool_names:
            flags.append(f"{len(turn.tool_names)} tool(s)")
        flag_str = " + ".join(flags) if flags else "text"
        chunks.append(f"[Turn {turn.index}  T+{wall_fmt}  {role}]  {flag_str}")
        for name, preview in zip(turn.tool_names, turn.tool_inputs_preview, strict=False):
            chunks.append(f"  Tool: {name}")
            if preview:
                chunks.append(f"    {preview}")
        if turn.text_preview:
            chunks.append(f"  {turn.text_preview!r}")
    return "\n".join(chunks)


def format_tools_timeline(traces: list[RoleTrace]) -> str:
    events: list[tuple[float, str, str, str]] = []
    for rt in traces:
        for turn in rt.turns:
            for name in turn.tool_names:
                events.append((turn.wall_s, rt.role, name, turn.timestamp))
    events.sort(key=lambda e: e[0])
    lines: list[str] = []
    for wall_s, role, name, _ts in events:
        lines.append(f"[T+{_fmt_wall(wall_s)}] {role}: {name}")
    return "\n".join(lines)


def format_tokens_table(summaries: list[RoleSummary]) -> str:
    headers = ("ROLE", "MODEL", "IN_TOK", "OUT_TOK", "CACHE_R", "CACHE_W")
    # cache_w not in RoleSummary — we don't sum it in summarize() to keep it simple;
    # leave it as 0 placeholder since it's not aggregated
    data = [
        (
            s.role,
            s.model,
            str(s.in_tok),
            str(s.out_tok),
            str(s.cache_r),
            "-",
        )
        for s in summaries
    ]
    widths = [
        max(len(h), *(len(row[i]) for row in data)) if data else len(h)
        for i, h in enumerate(headers)
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
    lines.extend(fmt.format(*row) for row in data)
    return "\n".join(lines)


def format_turn_detail(traces: list[RoleTrace], role: str, turn_n: int) -> str:
    rt = next((t for t in traces if t.role == role), None)
    if rt is None:
        return f"no trace found for role {role!r}"
    turns = rt.turns
    if not turns or turn_n < 1 or turn_n > len(turns):
        return f"turn {turn_n} out of range (role {role!r} has {len(turns)} turns)"
    turn = turns[turn_n - 1]
    lines = [f"[Turn {turn.index}  T+{_fmt_wall(turn.wall_s)}  {role}  model={turn.model}]"]
    lines.append(f"  in_tok={turn.in_tok} out_tok={turn.out_tok} cache_r={turn.cache_r}")
    if turn.thinking:
        lines.append("  <thinking block present>")
    for name, preview in zip(turn.tool_names, turn.tool_inputs_preview, strict=False):
        lines.append(f"  Tool: {name}")
        if preview:
            lines.append(f"    input: {preview}")
    if turn.text_preview:
        lines.append(f"  Text: {turn.text_preview!r}")
    return "\n".join(lines)


def format_slow_turns(traces: list[RoleTrace], min_secs: float) -> str:
    lines: list[str] = []
    for rt in traces:
        prev_wall = 0.0
        for turn in rt.turns:
            delta = turn.wall_s - prev_wall
            if delta >= min_secs:
                lines.append(
                    f"[T+{_fmt_wall(turn.wall_s)}] {rt.role} turn {turn.index}: "
                    f"{delta:.0f}s  tools={turn.tool_names}"
                )
            prev_wall = turn.wall_s
    return "\n".join(lines) if lines else f"no turns slower than {min_secs:.0f}s"


def to_json_list(traces: list[RoleTrace]) -> list[dict]:
    out: list[dict] = []
    for rt in traces:
        for turn in rt.turns:
            out.append(
                {
                    "role": rt.role,
                    "turn": turn.index,
                    "timestamp": turn.timestamp,
                    "type": "assistant",
                    "model": turn.model,
                    "thinking": turn.thinking,
                    "tool_names": turn.tool_names,
                    "content_preview": turn.text_preview,
                    "tokens": {
                        "input": turn.in_tok,
                        "output": turn.out_tok,
                        "cache_read": turn.cache_r,
                        "cache_write": turn.cache_w,
                    },
                    "wall_s": turn.wall_s,
                }
            )
    return out
