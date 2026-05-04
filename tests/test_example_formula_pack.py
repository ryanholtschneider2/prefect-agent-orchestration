"""Tests for the runnable example formula pack."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parents[1] / "packs" / "po-formulas-examples"
if str(PACK_ROOT) not in sys.path:
    sys.path.insert(0, str(PACK_ROOT))

from po_example_formulas.deployments import register
from po_example_formulas.flows import builder_heartbeat, on_bd_close, triage_inbox


def _init_dummy_rig(tmp_path: Path) -> Path:
    rig = tmp_path / "dummy-rig"
    rig.mkdir()
    subprocess.run(["git", "init"], cwd=rig, check=True, capture_output=True, text=True)
    return rig


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_triage_inbox_creates_bead_and_ready_item(tmp_path: Path) -> None:
    rig = _init_dummy_rig(tmp_path)
    inbox_dir = rig / ".po-example" / "inbox" / "default" / "untriaged"
    inbox_dir.mkdir(parents=True)
    (inbox_dir / "msg-1.json").write_text(
        json.dumps(
            {
                "message_id": "msg-1",
                "subject": "Bug report: fix the flaky test",
                "body": "Please take a look.",
                "route_hint": "create_bead",
                "follow_on_formula": "update-prompts-from-lessons",
            }
        )
    )

    result = triage_inbox.fn(rig_path=str(rig), account="default")

    assert result["processed"] == 1
    assert result["counts"] == {"create_bead": 1}
    bead = json.loads((rig / ".po-example" / "beads" / "inbox-msg-1.json").read_text())
    assert bead["status"] == "open"
    assert bead["metadata"]["target_role"] == "builder"
    ready = json.loads((rig / ".po-example" / "ready" / "builder.json").read_text())
    assert ready == [
        {
            "bead_id": "inbox-msg-1",
            "goal": "Bug report: fix the flaky test",
            "source_message_id": "msg-1",
        }
    ]
    archived = json.loads(
        (rig / ".po-example" / "inbox" / "default" / "triaged" / "msg-1.json").read_text()
    )
    assert archived["route"] == "create_bead"


def test_builder_heartbeat_consumes_queue_and_reuses_session(tmp_path: Path) -> None:
    rig = _init_dummy_rig(tmp_path)
    ready_path = rig / ".po-example" / "ready" / "builder.json"
    ready_path.parent.mkdir(parents=True)
    ready_path.write_text(
        json.dumps(
            [
                {"bead_id": "task-1", "goal": "Implement the first thing"},
                {"bead_id": "task-2", "goal": "Implement the second thing"},
            ]
        )
    )

    first = builder_heartbeat.fn(rig_path=str(rig), dry_run=True)
    second = builder_heartbeat.fn(rig_path=str(rig), dry_run=True)

    assert first["status"] == "worked"
    assert second["status"] == "worked"
    assert first["session_id"] == second["session_id"]
    remaining = json.loads(ready_path.read_text())
    assert remaining == []
    prompt_path = rig / ".po-example" / "prompts" / "builder.txt"
    assert "Bead: task-2" in prompt_path.read_text()
    runs = _read_jsonl(rig / ".po-example" / "heartbeat-runs.jsonl")
    assert [row["bead_id"] for row in runs] == ["task-1", "task-2"]


def test_on_bd_close_writes_follow_on_dispatch(tmp_path: Path) -> None:
    rig = _init_dummy_rig(tmp_path)
    beads_dir = rig / ".po-example" / "beads"
    beads_dir.mkdir(parents=True)
    (beads_dir / "task-9.json").write_text(
        json.dumps(
            {
                "id": "task-9",
                "status": "closed",
                "labels": ["retro"],
                "metadata": {"follow_on_formula": "update-prompts-from-lessons"},
            }
        )
    )

    result = on_bd_close.fn(rig_path=str(rig), bead_id="task-9")

    assert result == {
        "status": "triggered",
        "bead_id": "task-9",
        "formula": "update-prompts-from-lessons",
    }
    dispatches = _read_jsonl(rig / ".po-example" / "dispatches.jsonl")
    assert dispatches == [
        {
            "bead_id": "task-9",
            "formula": "update-prompts-from-lessons",
            "labels": ["retro"],
            "source": "on-bd-close",
        }
    ]


def test_register_exposes_example_deployments() -> None:
    names = [dep.name for dep in register()]
    assert names == [
        "builder-heartbeat-manual",
        "builder-heartbeat-workday",
        "triage-inbox-daily",
        "triage-inbox-manual",
        "on-bd-close-manual",
    ]
