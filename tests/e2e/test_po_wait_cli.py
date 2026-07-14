"""CLI regression for a terminal Prefect flow with an unclosed real bead."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

from typer.testing import CliRunner

from prefect_orchestration import cli


def test_po_wait_failed_flow_releases_exact_po_claim(monkeypatch, tmp_path) -> None:
    subprocess.run(
        ["bd", "init", "--prefix", "wait", "--quiet"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    created = subprocess.run(
        ["bd", "create", "terminal wait", "--type", "task", "--json"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    issue_id = json.loads(created.stdout)["id"]
    subprocess.run(
        [
            "bd",
            "update",
            issue_id,
            "--status",
            "in_progress",
            "--assignee",
            "po-deadbeef",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    flow_run = SimpleNamespace(
        id="deadbeef-0000-0000-0000-000000000000",
        state_name="Failed",
        state=SimpleNamespace(message="worker process exited"),
    )

    class FakeClientContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    async def fake_find(_client, **_kwargs):
        return [flow_run]

    import prefect.client.orchestration as orchestration
    from prefect_orchestration import wait as wait_helpers

    monkeypatch.setattr(orchestration, "get_client", lambda: FakeClientContext())
    monkeypatch.setattr(wait_helpers.status, "find_runs_by_issue_id", fake_find)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        cli.app,
        ["wait", issue_id, "--poll", "1", "--quiet"],
    )

    assert result.exit_code == 1, result.output
    assert "deadbeef-0000" in result.stderr
    assert "worker process exited" in result.stderr
    row_result = subprocess.run(
        ["bd", "show", issue_id, "--json"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    row = json.loads(row_result.stdout)
    if isinstance(row, list):
        row = row[0]
    assert row["status"] == "in_progress"
    assert not row.get("assignee")
    assert any(
        "po-wait-terminal:deadbeef-0000" in comment.get("text", "")
        for comment in row.get("comments", [])
    )
