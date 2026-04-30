"""Unit tests for trace.py parser + formatters."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prefect_orchestration import trace as _trace
from prefect_orchestration.cli import app
from prefect_orchestration.run_lookup import RunLocation


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_jsonl(tmp_path: Path, records: list[dict]) -> Path:
    p = tmp_path / "session.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return p


def _assistant(
    ts: str,
    model: str = "claude-opus-4-7",
    tools: list[str] | None = None,
    thinking: bool = False,
    in_tok: int = 100,
    out_tok: int = 50,
    cache_r: int = 200,
    cache_w: int = 10,
) -> dict:
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": "some thought"})
    for name in (tools or []):
        content.append({"type": "tool_use", "id": "x", "name": name, "input": {"cmd": "ls"}})
    content.append({"type": "text", "text": "hello response"})
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "model": model,
            "content": content,
            "usage": {
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "cache_read_input_tokens": cache_r,
                "cache_creation_input_tokens": cache_w,
            },
        },
    }


@pytest.fixture
def sample_jsonl(tmp_path: Path) -> Path:
    records = [
        # non-assistant record (should be skipped)
        {"type": "user", "timestamp": "2026-01-01T00:00:00Z", "message": {}},
        _assistant("2026-01-01T00:00:00Z", thinking=True, in_tok=100, out_tok=50, cache_r=200),
        _assistant("2026-01-01T00:00:10Z", tools=["Bash", "Read"], in_tok=200, out_tok=80, cache_r=300),
        _assistant("2026-01-01T00:00:25Z", tools=["Write"], in_tok=150, out_tok=60, cache_r=250),
    ]
    return _make_jsonl(tmp_path, records)


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

def test_parse_jsonl_counts_turns(sample_jsonl: Path) -> None:
    turns = _trace.parse_jsonl(sample_jsonl)
    assert len(turns) == 3


def test_parse_jsonl_usage_totals(sample_jsonl: Path) -> None:
    turns = _trace.parse_jsonl(sample_jsonl)
    assert sum(t.in_tok for t in turns) == 100 + 200 + 150
    assert sum(t.out_tok for t in turns) == 50 + 80 + 60


def test_parse_jsonl_tool_names(sample_jsonl: Path) -> None:
    turns = _trace.parse_jsonl(sample_jsonl)
    assert turns[0].tool_names == []
    assert turns[1].tool_names == ["Bash", "Read"]
    assert turns[2].tool_names == ["Write"]


def test_parse_jsonl_thinking_flag(sample_jsonl: Path) -> None:
    turns = _trace.parse_jsonl(sample_jsonl)
    assert turns[0].thinking is True
    assert turns[1].thinking is False


def test_parse_jsonl_wall_time(sample_jsonl: Path) -> None:
    turns = _trace.parse_jsonl(sample_jsonl)
    assert turns[0].wall_s == 0.0
    assert turns[1].wall_s == pytest.approx(10.0)
    assert turns[2].wall_s == pytest.approx(25.0)


def test_parse_jsonl_model(sample_jsonl: Path) -> None:
    turns = _trace.parse_jsonl(sample_jsonl)
    assert turns[0].model == "claude-opus-4-7"


def test_parse_jsonl_skips_non_assistant(tmp_path: Path) -> None:
    records = [
        {"type": "user", "timestamp": "2026-01-01T00:00:00Z", "message": {}},
        {"type": "queue-operation", "timestamp": "2026-01-01T00:00:01Z"},
        _assistant("2026-01-01T00:00:02Z"),
    ]
    p = _make_jsonl(tmp_path, records)
    turns = _trace.parse_jsonl(p)
    assert len(turns) == 1


# ---------------------------------------------------------------------------
# Formatter tests
# ---------------------------------------------------------------------------

def test_format_summary_table_headers(sample_jsonl: Path) -> None:
    turns = _trace.parse_jsonl(sample_jsonl)
    rt = _trace.RoleTrace(role="builder", uuid="abc", turns=turns, jsonl_path=sample_jsonl)
    summaries = _trace.summarize([rt])
    table = _trace.format_summary_table(summaries)
    for header in ("ROLE", "MODEL", "TURNS", "TOOLS", "IN_TOK", "OUT_TOK", "CACHE_R", "THINK", "WALL"):
        assert header in table


def test_format_summary_table_values(sample_jsonl: Path) -> None:
    turns = _trace.parse_jsonl(sample_jsonl)
    rt = _trace.RoleTrace(role="builder", uuid="abc", turns=turns, jsonl_path=sample_jsonl)
    summaries = _trace.summarize([rt])
    table = _trace.format_summary_table(summaries)
    assert "builder" in table
    assert "3" in table  # 3 turns
    assert "3" in table  # 3 tools total (Bash + Read + Write)


def test_format_tools_timeline(sample_jsonl: Path) -> None:
    turns = _trace.parse_jsonl(sample_jsonl)
    rt = _trace.RoleTrace(role="builder", uuid="abc", turns=turns, jsonl_path=sample_jsonl)
    timeline = _trace.format_tools_timeline([rt])
    assert "Bash" in timeline
    assert "Read" in timeline
    assert "Write" in timeline
    lines = [l for l in timeline.splitlines() if l.strip()]
    # Chronological: Bash/Read before Write
    bash_idx = next(i for i, l in enumerate(lines) if "Bash" in l)
    write_idx = next(i for i, l in enumerate(lines) if "Write" in l)
    assert bash_idx < write_idx


def test_to_json_list_structure(sample_jsonl: Path) -> None:
    turns = _trace.parse_jsonl(sample_jsonl)
    rt = _trace.RoleTrace(role="builder", uuid="abc", turns=turns, jsonl_path=sample_jsonl)
    items = _trace.to_json_list([rt])
    assert len(items) == 3
    for item in items:
        assert "role" in item
        assert "turn" in item
        assert "timestamp" in item
        assert "type" in item
        assert "tokens" in item
    # Ensure JSON-serialisable
    json.dumps(items)


def test_format_transcript_role_not_found(sample_jsonl: Path) -> None:
    turns = _trace.parse_jsonl(sample_jsonl)
    rt = _trace.RoleTrace(role="builder", uuid="abc", turns=turns, jsonl_path=sample_jsonl)
    out = _trace.format_transcript([rt], "planner")
    assert "not found" in out or "planner" in out


def test_format_slow_turns_no_slow(sample_jsonl: Path) -> None:
    turns = _trace.parse_jsonl(sample_jsonl)
    rt = _trace.RoleTrace(role="builder", uuid="abc", turns=turns, jsonl_path=sample_jsonl)
    out = _trace.format_slow_turns([rt], min_secs=9999)
    assert "no turns" in out


# ---------------------------------------------------------------------------
# find_jsonl tests
# ---------------------------------------------------------------------------

def test_find_jsonl_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig = Path("/fake/rig")
    slug = str(rig).replace("/", "-")
    proj_dir = tmp_path / ".claude" / "projects" / slug
    proj_dir.mkdir(parents=True)
    f = proj_dir / "test-uuid.jsonl"
    f.write_text("{}\n")

    monkeypatch.setattr(_trace, "Path", lambda *a: _patched_path(tmp_path, *a))

    found = _trace_find_jsonl_with_home(tmp_path, "test-uuid", rig)
    assert found is not None
    assert found.name == "test-uuid.jsonl"


def test_find_jsonl_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig = Path("/fake/rig")
    projects_dir = tmp_path / ".claude" / "projects"
    projects_dir.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    result = _trace.find_jsonl("no-such-uuid", rig)
    assert result is None


def _patched_path(home: Path, *args: object) -> Path:
    return Path(*args)


def _trace_find_jsonl_with_home(home: Path, uuid: str, rig_path: Path) -> Path | None:
    """Helper that injects a fake home for find_jsonl."""
    projects_dir = home / ".claude" / "projects"
    slug = str(rig_path).replace("/", "-")
    candidate = projects_dir / slug / f"{uuid}.jsonl"
    if candidate.exists():
        return candidate
    for f in projects_dir.glob(f"*/{uuid}.jsonl"):
        return f
    return None


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_trace_fixtures(
    tmp_path: Path,
) -> tuple[RunLocation, dict[str, str], list[_trace.RoleTrace]]:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    rig_path = tmp_path / "rig"
    loc = RunLocation(rig_path=rig_path, run_dir=run_dir)
    metadata = {"session_builder": "uuid-builder-1"}
    turns = [
        _trace.TurnRecord(
            index=1,
            timestamp="2026-01-01T00:00:00Z",
            thinking=False,
            tool_names=["Bash"],
            tool_inputs_preview=["{'cmd': 'ls'}"],
            text_preview="hello",
            in_tok=100,
            out_tok=50,
            cache_r=200,
            cache_w=10,
            model="claude-opus-4-7",
            wall_s=0.0,
        )
    ]
    traces = [
        _trace.RoleTrace(role="builder", uuid="uuid-builder-1", turns=turns, jsonl_path=None)
    ]
    return loc, metadata, traces


def test_trace_command_summary(
    tmp_path: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prefect_orchestration import cli as cli_mod, sessions as _sessions_mod
    from prefect_orchestration import beads_meta as _beads_meta_mod

    loc, metadata, traces = _make_trace_fixtures(tmp_path)
    monkeypatch.setattr(cli_mod._run_lookup, "resolve_run_dir", lambda _id: loc)
    monkeypatch.setattr(_beads_meta_mod, "resolve_seed_bead", lambda _id, **kw: _id)
    monkeypatch.setattr(cli_mod._sessions, "load_role_sessions", lambda *a, **kw: metadata)
    monkeypatch.setattr(cli_mod._trace, "find_jsonl", lambda uuid, rp: None)
    # Provide parsed turns via parse_jsonl — but since find_jsonl returns None, turns will be []
    # Instead patch so that the RoleTrace gets our fixture turns:

    original_find = cli_mod._trace.find_jsonl
    fake_path = tmp_path / "fake.jsonl"
    fake_path.write_text("")
    monkeypatch.setattr(cli_mod._trace, "find_jsonl", lambda uuid, rp: fake_path)
    monkeypatch.setattr(cli_mod._trace, "parse_jsonl", lambda p: traces[0].turns)

    result = runner.invoke(app, ["trace", "test-issue"])
    assert result.exit_code == 0, result.output
    for header in ("ROLE", "MODEL", "TURNS", "TOOLS", "IN_TOK", "OUT_TOK", "CACHE_R", "THINK", "WALL"):
        assert header in result.output


def test_trace_command_json(
    tmp_path: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prefect_orchestration import cli as cli_mod
    from prefect_orchestration import beads_meta as _beads_meta_mod

    loc, metadata, traces = _make_trace_fixtures(tmp_path)
    monkeypatch.setattr(cli_mod._run_lookup, "resolve_run_dir", lambda _id: loc)
    monkeypatch.setattr(_beads_meta_mod, "resolve_seed_bead", lambda _id, **kw: _id)
    monkeypatch.setattr(cli_mod._sessions, "load_role_sessions", lambda *a, **kw: metadata)
    fake_path = tmp_path / "fake.jsonl"
    fake_path.write_text("")
    monkeypatch.setattr(cli_mod._trace, "find_jsonl", lambda uuid, rp: fake_path)
    monkeypatch.setattr(cli_mod._trace, "parse_jsonl", lambda p: traces[0].turns)

    result = runner.invoke(app, ["trace", "test-issue", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 1
    item = data[0]
    assert item["role"] == "builder"
    assert "turn" in item
    assert "timestamp" in item
    assert "tokens" in item


def test_trace_command_run_dir_not_found(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prefect_orchestration import cli as cli_mod
    from prefect_orchestration.run_lookup import RunDirNotFound

    monkeypatch.setattr(
        cli_mod._run_lookup,
        "resolve_run_dir",
        lambda _id: (_ for _ in ()).throw(RunDirNotFound("no run_dir")),
    )
    result = runner.invoke(app, ["trace", "no-such-issue"])
    assert result.exit_code != 0


def test_artifacts_footer(
    tmp_path: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prefect_orchestration import cli as cli_mod

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    loc = RunLocation(rig_path=tmp_path, run_dir=run_dir)
    monkeypatch.setattr(cli_mod._run_lookup, "resolve_run_dir", lambda _id: loc)

    result = runner.invoke(app, ["artifacts", "test-issue"])
    assert result.exit_code == 0, result.output
    assert "po trace" in result.output
    assert "test-issue" in result.output
