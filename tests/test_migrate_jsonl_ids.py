"""Unit tests for `setup/migrate_jsonl_ids.py` (dolt->br id rewrite)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_MOD_PATH = Path(__file__).resolve().parent.parent / "setup" / "migrate_jsonl_ids.py"
_spec = importlib.util.spec_from_file_location("migrate_jsonl_ids", _MOD_PATH)
assert _spec and _spec.loader
migrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate)


def test_rewrite_record_hyphenates_iter_ids() -> None:
    """Dotted iter ids in id-bearing fields become hyphenated."""
    rec = {
        "id": "courtpro-0qt.ralph.iter1",
        "title": "do the thing",  # untouched
        "depends_on_id": "courtpro-0qt.build.iter2",
    }
    out = migrate.rewrite_record(rec)
    assert out["id"] == "courtpro-0qt-ralph-iter1"
    assert out["depends_on_id"] == "courtpro-0qt-build-iter2"
    assert out["title"] == "do the thing"


def test_rewrite_record_leaves_dotless_seed_ids() -> None:
    """Seed/parent ids have no dots — the rewrite is a no-op on them."""
    rec = {"id": "prefect-orchestration-5w3", "issue_id": "iss-1"}
    out = migrate.rewrite_record(rec)
    assert out == {"id": "prefect-orchestration-5w3", "issue_id": "iss-1"}


def test_rewrite_record_recurses_nested_dependencies() -> None:
    """Embedded dependency rows are rewritten too."""
    rec = {
        "id": "seed.plan.iter1",
        "dependencies": [
            {"issue_id": "seed.plan.iter1", "depends_on_id": "seed", "type": "blocks"}
        ],
    }
    out = migrate.rewrite_record(rec)
    assert out["id"] == "seed-plan-iter1"
    assert out["dependencies"][0]["issue_id"] == "seed-plan-iter1"
    assert out["dependencies"][0]["depends_on_id"] == "seed"  # dotless, untouched
    assert out["dependencies"][0]["type"] == "blocks"


def test_rewrite_record_does_not_touch_non_id_dotted_strings() -> None:
    """Only id-bearing keys are rewritten — prose keeps its dots."""
    rec = {"id": "seed", "notes": "v1.2.3 released. see docs."}
    out = migrate.rewrite_record(rec)
    assert out["notes"] == "v1.2.3 released. see docs."


def test_rewrite_jsonl_text_per_line() -> None:
    """Each JSONL line is rewritten independently; blanks preserved."""
    text = (
        json.dumps({"id": "a.triage.iter1"})
        + "\n\n"
        + json.dumps({"id": "b.build.iter2"})
        + "\n"
    )
    out = migrate.rewrite_jsonl_text(text)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert json.loads(lines[0])["id"] == "a-triage-iter1"
    assert json.loads(lines[1])["id"] == "b-build-iter2"
    assert "\n\n" in out  # blank line survived


def test_rewrite_jsonl_text_passes_through_non_json() -> None:
    """Non-JSON lines are emitted verbatim (lenient)."""
    out = migrate.rewrite_jsonl_text("not json\n")
    assert out == "not json\n"
