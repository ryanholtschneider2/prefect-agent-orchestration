from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from prefect_orchestration import reconcile


class FakeClient:
    async def read_flow_runs(self, **kwargs):
        assert kwargs["limit"] == 200
        assert kwargs["offset"] == 0
        return [
            SimpleNamespace(
                id="run-1",
                name="issue-1",
                tags=["issue_id:issue-1"],
                state_name="Running",
                parameters={"rig_path": "/tmp/rig"},
                created=None,
                start_time=None,
                expected_start_time=None,
            )
        ]


@pytest.mark.asyncio
async def test_find_abandoned_requires_stale_and_no_live_process(monkeypatch) -> None:
    monkeypatch.setattr(reconcile.status, "compute_stale_secs", lambda *a, **k: 900)
    monkeypatch.setattr(reconcile.status, "_has_live_process", lambda _issue: False)

    found = await reconcile._find_abandoned(FakeClient(), 600)

    assert found == [("issue-1", "run-1")]


@pytest.mark.asyncio
async def test_find_abandoned_pages_past_prefect_limit(monkeypatch) -> None:
    pages = [
        [SimpleNamespace(id=f"run-{i}") for i in range(200)],
        [SimpleNamespace(id="run-last")],
    ]
    offsets: list[int] = []

    async def find_page(_client, **kwargs):
        offsets.append(kwargs["offset"])
        return pages[len(offsets) - 1]

    monkeypatch.setattr(reconcile.status, "find_runs_by_issue_id", find_page)
    monkeypatch.setattr(reconcile.status, "group_by_issue", lambda _runs: [])

    assert await reconcile._find_abandoned(object(), 600) == []
    assert offsets == [0, 200]


def test_claim_marker_is_idempotent(tmp_path: Path) -> None:
    first = reconcile._claim_marker(tmp_path, "run-1")
    second = reconcile._claim_marker(tmp_path, "run-1")

    assert first is not None
    assert second is None
