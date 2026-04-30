"""Unit tests for prefect_orchestration.spend, plus JSON helpers in watch/status/sessions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from prefect_orchestration import spend as _spend
from prefect_orchestration import watch as _watch
from prefect_orchestration import status as _status
from prefect_orchestration import sessions as _sessions
from prefect_orchestration.spend import (
    FALLBACK_MODEL,
    SpendRecord,
    _compute_cost,
    aggregate,
    build_records,
    discover_run_dirs,
    render_table,
    to_json,
)
from prefect_orchestration.watch import Event, render_ndjson
from prefect_orchestration.status import IssueGroup, to_json_list as status_to_json_list
from prefect_orchestration.sessions import SessionRow, to_json_list as sessions_to_json_list


# ─── cost math ───────────────────────────────────────────────────────


def test_cost_from_summary_sonnet():
    """Cost = (in/out/cr/cw tokens / 1e6) * respective per-MTok prices."""
    model = "claude-sonnet-4-6"
    in_tok = 1_000_000
    out_tok = 1_000_000
    cache_r = 1_000_000
    cache_w = 1_000_000
    cost = _compute_cost(model, in_tok, out_tok, cache_r, cache_w)
    p = _spend.PRICING[model]
    expected = p["in"] + p["out"] + p["cr"] + p["cw"]
    assert abs(cost - expected) < 0.0001


def test_cost_fallback_model():
    """Unknown model falls back to FALLBACK_MODEL pricing without raising."""
    cost = _compute_cost("claude-unknown-model", 1_000_000, 0, 0, 0)
    fallback_price = _spend.PRICING[FALLBACK_MODEL]["in"]
    assert abs(cost - fallback_price) < 0.0001


def test_cost_zero_tokens():
    cost = _compute_cost("claude-sonnet-4-6", 0, 0, 0, 0)
    assert cost == 0.0


# ─── aggregate ───────────────────────────────────────────────────────


def _make_record(role: str, day: str = "2026-04-29", formula: str = "f1") -> SpendRecord:
    return SpendRecord(
        formula=formula,
        issue_id="test-1",
        role=role,
        model="claude-sonnet-4-6",
        day=day,
        in_tok=100,
        out_tok=50,
        cache_r_tok=200,
        cache_w_tok=10,
        cost_usd=0.01,
    )


def test_aggregate_by_role_sums_tokens():
    r1 = _make_record("builder")
    r2 = _make_record("builder")
    rows = aggregate([r1, r2], by="role")
    assert len(rows) == 1
    row = rows[0]
    assert row["role"] == "builder"
    assert row["in_tok"] == 200
    assert row["out_tok"] == 100
    assert row["cache_r_tok"] == 400
    assert abs(row["cost_usd"] - 0.02) < 0.0001
    assert row["records"] == 2


def test_aggregate_by_role_multiple_roles():
    records = [_make_record("builder"), _make_record("triager"), _make_record("builder")]
    rows = aggregate(records, by="role")
    assert len(rows) == 2
    roles = {r["role"] for r in rows}
    assert roles == {"builder", "triager"}


def test_aggregate_by_day():
    r1 = _make_record("builder", day="2026-04-28")
    r2 = _make_record("builder", day="2026-04-29")
    r3 = _make_record("triager", day="2026-04-28")
    rows = aggregate([r1, r2, r3], by="day")
    assert len(rows) == 2
    days = {r["day"] for r in rows}
    assert "2026-04-28" in days
    assert "2026-04-29" in days
    # Apr-28 has two records
    apr28 = next(r for r in rows if r["day"] == "2026-04-28")
    assert apr28["records"] == 2


def test_aggregate_by_formula():
    r1 = _make_record("builder", formula="software-dev-full")
    r2 = _make_record("builder", formula="minimal-task")
    rows = aggregate([r1, r2], by="formula")
    assert len(rows) == 2


# ─── to_json shape ───────────────────────────────────────────────────


def test_to_json_shape():
    records = [_make_record("builder")]
    result = to_json(records)
    assert len(result) == 1
    rec = result[0]
    for field in ("formula", "issue_id", "role", "model", "day", "in_tok", "out_tok",
                  "cache_r_tok", "cache_w_tok", "cost_usd", "pricing_note"):
        assert field in rec, f"missing field: {field}"
    assert isinstance(rec["in_tok"], int)
    assert isinstance(rec["cost_usd"], float)
    assert isinstance(rec["pricing_note"], str)


def test_to_json_serializable():
    """Ensure the output can be round-tripped through json.dumps."""
    records = [_make_record("builder"), _make_record("triager")]
    result = to_json(records)
    parsed = json.loads(json.dumps(result))
    assert len(parsed) == 2


# ─── render_table smoke ──────────────────────────────────────────────


def test_render_table_empty():
    assert render_table([], by="role") == "no spend data found."


def test_render_table_has_cost_column():
    rows = aggregate([_make_record("builder")], by="role")
    table = render_table(rows, by="role")
    assert "COST_USD" in table
    assert "builder" in table


# ─── watch.render_ndjson ─────────────────────────────────────────────


def test_render_ndjson_valid_json():
    ev = Event(
        ts=datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc),
        source="prefect",
        kind="state",
        text="Pending → Running",
    )
    line = render_ndjson(ev)
    parsed = json.loads(line)
    assert parsed["source"] == "prefect"
    assert parsed["kind"] == "state"
    assert parsed["text"] == "Pending → Running"
    assert "2026-04-29" in parsed["ts"]


def test_render_ndjson_fields():
    ev = Event(
        ts=datetime(2026, 4, 29, tzinfo=timezone.utc),
        source="run-dir",
        kind="new",
        text="plan.md",
    )
    parsed = json.loads(render_ndjson(ev))
    for key in ("ts", "source", "kind", "text"):
        assert key in parsed


# ─── status.to_json_list ─────────────────────────────────────────────


@dataclass
class _FakeFlowRun:
    id: str = "abc123"
    name: str = "test-flow"
    tags: list = None  # type: ignore[assignment]
    state_name: str = "Running"
    start_time: datetime | None = None
    end_time: datetime | None = None
    expected_start_time: datetime | None = None
    parameters: dict | None = None

    def __post_init__(self) -> None:
        if self.tags is None:
            self.tags = []


def test_status_to_json_list():
    fr = _FakeFlowRun(id="abc123", tags=["issue_id:test-1"])
    group = IssueGroup(issue_id="test-1", latest=fr, extras=[])
    result = status_to_json_list([group])
    assert len(result) == 1
    row = result[0]
    assert row["issue_id"] == "test-1"
    assert "state" in row
    assert "run_count" in row
    assert row["run_count"] == 1


def test_status_to_json_list_serializable():
    fr = _FakeFlowRun(id="abc123")
    group = IssueGroup(issue_id="test-1", latest=fr, extras=[fr])
    result = status_to_json_list([group])
    # Must be JSON-serializable
    json.dumps(result)
    assert result[0]["run_count"] == 2


# ─── sessions.to_json_list ───────────────────────────────────────────


def test_sessions_to_json_list():
    rows = [
        SessionRow(role="builder", uuid="uuid-builder", last_iter="2", last_updated="2026-04-29 12:00:00"),
        SessionRow(role="triager", uuid="uuid-triager", last_iter="-", last_updated="2026-04-29 11:00:00"),
    ]
    result = sessions_to_json_list(rows)
    assert len(result) == 2
    assert result[0]["role"] == "builder"
    assert result[0]["uuid"] == "uuid-builder"
    assert result[0]["last_iter"] == "2"
    assert "pod" in result[0]


def test_sessions_to_json_list_serializable():
    rows = [SessionRow(role="builder", uuid="uuid-1", last_iter="1", last_updated="-")]
    json.dumps(sessions_to_json_list(rows))


# ─── discover_run_dirs ───────────────────────────────────────────────


def test_discover_run_dirs_empty(tmp_path: Path):
    assert discover_run_dirs(tmp_path) == []


def test_discover_run_dirs_finds_dirs(tmp_path: Path):
    planning = tmp_path / ".planning" / "software-dev-full"
    planning.mkdir(parents=True)
    run1 = planning / "issue-1"
    run1.mkdir()
    (run1 / "metadata.json").write_text("{}")

    results = discover_run_dirs(tmp_path)
    assert len(results) == 1
    formula, issue_id, run_dir = results[0]
    assert formula == "software-dev-full"
    assert issue_id == "issue-1"
    assert run_dir == run1


def test_discover_run_dirs_since_filter(tmp_path: Path):
    import time
    import os

    planning = tmp_path / ".planning" / "f1"
    planning.mkdir(parents=True)
    old_dir = planning / "old-issue"
    old_dir.mkdir()
    new_dir = planning / "new-issue"
    new_dir.mkdir()

    # Set old_dir mtime to 1 hour ago
    old_ts = time.time() - 3600
    os.utime(old_dir, (old_ts, old_ts))

    since = datetime.now(timezone.utc).replace(microsecond=0)
    # new_dir mtime is now, which is >= since
    results = discover_run_dirs(tmp_path, since=since)
    issue_ids = [r[1] for r in results]
    assert "new-issue" in issue_ids
    assert "old-issue" not in issue_ids
