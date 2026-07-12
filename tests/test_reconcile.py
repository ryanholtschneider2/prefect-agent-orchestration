from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from prefect_orchestration import reconcile


class FakeClient:
    async def read_flow_runs(self, **kwargs):
        assert kwargs["limit"] == 200
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


def test_claim_marker_is_idempotent(tmp_path: Path) -> None:
    first = reconcile._claim_marker(tmp_path, "run-1")
    second = reconcile._claim_marker(tmp_path, "run-1")

    assert first is not None
    assert second is None
