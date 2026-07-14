"""Unit tests for Prefect-terminal observation and safe bead reconciliation."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from prefect_orchestration import wait


def test_latest_terminal_flow_returns_failure_details(monkeypatch) -> None:
    flow_run = SimpleNamespace(
        id="abcd1234-full",
        state_name="Crashed",
        state=SimpleNamespace(message="worker heartbeat expired"),
    )

    class Client:
        pass

    class ClientContext:
        async def __aenter__(self):
            return Client()

        async def __aexit__(self, *exc):
            return False

    async def fake_find(client, **kwargs):
        assert kwargs == {"issue_id": "work-1", "limit": 1}
        return [flow_run]

    import prefect.client.orchestration as orchestration

    monkeypatch.setattr(orchestration, "get_client", lambda: ClientContext())
    monkeypatch.setattr(wait.status, "find_runs_by_issue_id", fake_find)

    assert wait.latest_terminal_flow("work-1") == wait.TerminalFlow(
        flow_run_id="abcd1234-full",
        state="Crashed",
        message="worker heartbeat expired",
    )


def test_latest_terminal_flow_ignores_completed_and_api_failure(monkeypatch) -> None:
    class ClientContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    import prefect.client.orchestration as orchestration

    monkeypatch.setattr(orchestration, "get_client", lambda: ClientContext())

    async def completed(_client, **_kwargs):
        return [SimpleNamespace(id="ok", state_name="Completed")]

    monkeypatch.setattr(wait.status, "find_runs_by_issue_id", completed)
    assert wait.latest_terminal_flow("work-1") is None

    async def unavailable(_client, **_kwargs):
        raise OSError("server unavailable")

    monkeypatch.setattr(wait.status, "find_runs_by_issue_id", unavailable)
    assert wait.latest_terminal_flow("work-1") is None


def test_reconcile_annotates_and_clears_only_matching_po_claim(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(wait, "_resolve_binary", lambda _path: "bd")

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(wait.subprocess, "run", fake_run)
    terminal = wait.TerminalFlow("12345678-rest", "Failed", "capacity exhausted")

    cleared = wait.reconcile_failed_claim(
        "work-1",
        {"status": "in_progress", "assignee": "po-12345678"},
        terminal,
        rig_path=tmp_path,
    )

    assert cleared is True
    assert calls[0][:4] == ["bd", "comments", "add", "work-1"]
    assert "no success verdict" in calls[0][4]
    assert calls[1] == ["bd", "update", "work-1", "--assignee", ""]


def test_reconcile_preserves_human_or_different_po_claim(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(wait, "_resolve_binary", lambda _path: "bd")

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(wait.subprocess, "run", fake_run)
    terminal = wait.TerminalFlow("12345678-rest", "Cancelled", "operator cancel")

    cleared = wait.reconcile_failed_claim(
        "work-1",
        {"status": "in_progress", "assignee": "po-87654321"},
        terminal,
        rig_path=None,
    )

    assert cleared is False
    assert len(calls) == 1
    assert calls[0][:3] == ["bd", "comments", "add"]


def test_reconcile_comment_is_idempotent(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(wait, "_resolve_binary", lambda _path: "bd")

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(wait.subprocess, "run", fake_run)
    terminal = wait.TerminalFlow("12345678-rest", "Failed", "boom")
    row = {
        "status": "in_progress",
        "assignee": "ryan",
        "comments": [{"text": "po-wait-terminal:12345678-rest already noted"}],
    }

    wait.reconcile_failed_claim("work-1", row, terminal, rig_path=None)

    assert calls == []
