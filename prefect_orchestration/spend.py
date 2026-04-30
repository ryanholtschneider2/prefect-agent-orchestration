"""Token spend aggregation for PO runs.

Reads per-role JSONL traces from ~/.claude/projects/<slug>/<uuid>.jsonl
(via `trace.find_jsonl` + `trace.parse_jsonl`) and computes estimated
USD cost using a hardcoded pricing table. Best-effort estimation — po
uses the Agent SDK / OAuth, not API keys, so this is not billing-grade.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prefect_orchestration import trace as _trace
from prefect_orchestration.sessions import METADATA_FILENAME

# Per-MTok pricing (USD). Keys are model-id prefixes (longest match wins).
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {"in": 15.0, "out": 75.0, "cr": 1.5, "cw": 18.75},
    "claude-sonnet-4-6": {"in": 3.0, "out": 15.0, "cr": 0.30, "cw": 3.75},
    "claude-haiku-4-5": {"in": 0.80, "out": 4.0, "cr": 0.08, "cw": 1.0},
}
FALLBACK_MODEL = "claude-sonnet-4-6"

PRICING_NOTE = (
    "Prices are estimated (per-MTok, USD) based on hardcoded table; "
    "subject to Anthropic pricing changes. Not billing-grade."
)


def _model_pricing(model: str) -> dict[str, float]:
    """Return pricing dict for a model, falling back to FALLBACK_MODEL pricing."""
    for prefix, prices in PRICING.items():
        if model.startswith(prefix):
            return prices
    return PRICING[FALLBACK_MODEL]


@dataclass
class SpendRecord:
    formula: str
    issue_id: str
    role: str
    model: str
    day: str  # YYYY-MM-DD derived from run_dir mtime
    in_tok: int
    out_tok: int
    cache_r_tok: int
    cache_w_tok: int
    cost_usd: float


def _compute_cost(model: str, in_tok: int, out_tok: int, cache_r: int, cache_w: int) -> float:
    p = _model_pricing(model)
    return (
        in_tok / 1_000_000 * p["in"]
        + out_tok / 1_000_000 * p["out"]
        + cache_r / 1_000_000 * p["cr"]
        + cache_w / 1_000_000 * p["cw"]
    )


def _day_from_mtime(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d")


def _build_records_from_run_dir(
    run_dir: Path,
    *,
    formula: str,
    issue_id: str,
    rig_path: Path,
) -> list[SpendRecord]:
    """Read metadata.json from a run_dir, parse each role's JSONL, return SpendRecords."""
    meta_path = run_dir / METADATA_FILENAME
    if not meta_path.exists():
        return []
    try:
        metadata: dict[str, str] = json.loads(meta_path.read_text())
    except Exception:
        return []

    run_mtime = run_dir.stat().st_mtime
    day = _day_from_mtime(run_mtime)
    records: list[SpendRecord] = []

    for key, uuid in metadata.items():
        if not key.startswith("session_"):
            continue
        role = key[len("session_"):]
        jsonl_path = _trace.find_jsonl(uuid, rig_path)
        if jsonl_path is None:
            continue
        try:
            turns = _trace.parse_jsonl(jsonl_path)
        except Exception:
            continue
        if not turns:
            continue
        # Use first non-unknown model found in turns
        model = FALLBACK_MODEL
        for t in turns:
            if t.model and t.model != "unknown":
                model = t.model
                break
        in_tok = sum(t.in_tok for t in turns)
        out_tok = sum(t.out_tok for t in turns)
        cache_r = sum(t.cache_r for t in turns)
        cache_w = sum(t.cache_w for t in turns)
        cost = _compute_cost(model, in_tok, out_tok, cache_r, cache_w)
        records.append(
            SpendRecord(
                formula=formula,
                issue_id=issue_id,
                role=role,
                model=model,
                day=day,
                in_tok=in_tok,
                out_tok=out_tok,
                cache_r_tok=cache_r,
                cache_w_tok=cache_w,
                cost_usd=cost,
            )
        )
    return records


def discover_run_dirs(
    rig_path: Path, *, since: datetime | None = None
) -> list[tuple[str, str, Path]]:
    """Yield (formula, issue_id, run_dir) for all run_dirs under .planning/."""
    planning = rig_path / ".planning"
    if not planning.is_dir():
        return []
    result: list[tuple[str, str, Path]] = []
    for formula_dir in planning.iterdir():
        if not formula_dir.is_dir():
            continue
        formula = formula_dir.name
        for run_dir in formula_dir.iterdir():
            if not run_dir.is_dir():
                continue
            if since is not None:
                try:
                    mtime = run_dir.stat().st_mtime
                    run_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
                    if run_dt < since:
                        continue
                except OSError:
                    continue
            result.append((formula, run_dir.name, run_dir))
    return result


def build_records(
    run_dirs: list[tuple[str, str, Path]], *, rig_path: Path
) -> list[SpendRecord]:
    """Build SpendRecord list from a list of (formula, issue_id, run_dir) tuples."""
    records: list[SpendRecord] = []
    for formula, issue_id, run_dir in run_dirs:
        records.extend(
            _build_records_from_run_dir(
                run_dir,
                formula=formula,
                issue_id=issue_id,
                rig_path=rig_path,
            )
        )
    return records


def aggregate(records: list[SpendRecord], by: str) -> list[dict[str, Any]]:
    """Group and sum records by 'formula'|'role'|'day'. Returns list of row dicts."""
    buckets: dict[str, dict[str, Any]] = {}
    for r in records:
        if by == "formula":
            key = r.formula
        elif by == "role":
            key = r.role
        elif by == "day":
            key = r.day
        else:
            key = r.role  # default fallback
        if key not in buckets:
            buckets[key] = {
                by: key,
                "in_tok": 0,
                "out_tok": 0,
                "cache_r_tok": 0,
                "cache_w_tok": 0,
                "cost_usd": 0.0,
                "records": 0,
            }
        b = buckets[key]
        b["in_tok"] += r.in_tok
        b["out_tok"] += r.out_tok
        b["cache_r_tok"] += r.cache_r_tok
        b["cache_w_tok"] += r.cache_w_tok
        b["cost_usd"] += r.cost_usd
        b["records"] += 1
    rows = list(buckets.values())
    rows.sort(key=lambda x: x["cost_usd"], reverse=True)
    return rows


def render_table(rows: list[dict[str, Any]], by: str) -> str:
    if not rows:
        return "no spend data found."
    label = by.upper()
    headers = (label, "RECORDS", "IN_TOK", "OUT_TOK", "CACHE_R", "CACHE_W", "COST_USD")
    data = [
        (
            str(r[by]),
            str(r["records"]),
            str(r["in_tok"]),
            str(r["out_tok"]),
            str(r["cache_r_tok"]),
            str(r["cache_w_tok"]),
            f"${r['cost_usd']:.4f}",
        )
        for r in rows
    ]
    widths = [
        max(len(h), *(len(row[i]) for row in data)) if data else len(h)
        for i, h in enumerate(headers)
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
    lines.extend(fmt.format(*row) for row in data)
    total_cost = sum(r["cost_usd"] for r in rows)
    lines.append(fmt.format(*("-" * w for w in widths)))
    lines.append(f"total: ${total_cost:.4f}")
    return "\n".join(lines)


def to_json(records: list[SpendRecord]) -> list[dict[str, Any]]:
    """Serialise SpendRecord list to JSON-safe dicts."""
    return [
        {
            "formula": r.formula,
            "issue_id": r.issue_id,
            "role": r.role,
            "model": r.model,
            "day": r.day,
            "in_tok": r.in_tok,
            "out_tok": r.out_tok,
            "cache_r_tok": r.cache_r_tok,
            "cache_w_tok": r.cache_w_tok,
            "cost_usd": r.cost_usd,
            "pricing_note": PRICING_NOTE,
        }
        for r in records
    ]
