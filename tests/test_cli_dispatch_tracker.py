"""Dispatch preflight coverage for caller/rig tracker mismatches."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from prefect_orchestration import cli


def _tracker(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / ".beads").mkdir()
    return path


@pytest.mark.parametrize("target_key", ["issue_id", "epic_id", "root_id"])
def test_validate_rejects_bead_only_in_caller_tracker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target_key: str
) -> None:
    caller = _tracker(tmp_path / "caller")
    rig = _tracker(tmp_path / "rig")
    bead_id = "caller-123"

    def fake_show(requested: str, rig_path: Path) -> dict | None:
        assert requested == bead_id
        return {"id": bead_id} if Path(rig_path).resolve() == caller else None

    monkeypatch.setattr(cli._beads_meta, "_bd_show", fake_show)

    with pytest.raises(cli.DispatchTrackerMismatch) as raised:
        cli._validate_dispatch_tracker(
            {target_key: bead_id, "rig_path": str(rig)}, caller_path=caller
        )

    message = str(raised.value)
    assert str(caller / ".beads") in message
    assert str(rig / ".beads") in message
    assert "No Prefect flow was submitted" in message


def test_validate_preserves_nested_polyrepo_rig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    caller = _tracker(tmp_path / "polyrepo")
    nested_code = caller / "services" / "api"
    nested_code.mkdir(parents=True)
    show = Mock(side_effect=AssertionError("same tracker should not be probed"))
    monkeypatch.setattr(cli._beads_meta, "_bd_show", show)

    cli._validate_dispatch_tracker(
        {"issue_id": "poly-1", "rig_path": str(nested_code)},
        caller_path=caller,
    )

    show.assert_not_called()


def test_validate_allows_separate_tracker_when_bead_exists_in_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    caller = _tracker(tmp_path / "caller")
    rig = _tracker(tmp_path / "rig")
    monkeypatch.setattr(
        cli._beads_meta, "_bd_show", lambda _bead_id, rig_path: {"id": "shared-1"}
    )

    cli._validate_dispatch_tracker(
        {"issue_id": "shared-1", "rig_path": str(rig)}, caller_path=caller
    )


@pytest.mark.parametrize("dispatch_args", [["--foreground"], ["--at", "2h"]])
def test_cli_fails_before_foreground_or_scheduled_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dispatch_args: list[str],
) -> None:
    caller = _tracker(tmp_path / "caller")
    rig = _tracker(tmp_path / "rig")
    bead_id = "caller-456"
    flow_called = Mock()
    submit_called = Mock()

    def flow(issue_id: str, rig_path: str) -> str:
        flow_called(issue_id, rig_path)
        return "unexpected"

    def fake_show(requested: str, rig_path: Path) -> dict | None:
        assert requested == bead_id
        return {"id": bead_id} if Path(rig_path).resolve() == caller else None

    monkeypatch.chdir(caller)
    monkeypatch.setattr(cli, "_load_formulas", lambda: {"demo": flow})
    monkeypatch.setattr(cli, "_autoconfigure_prefect_api", lambda: None)
    monkeypatch.setattr(cli._beads_meta, "_bd_show", fake_show)
    monkeypatch.setattr(cli._scheduling, "submit_scheduled_run", submit_called)

    result = CliRunner().invoke(
        cli.app,
        [
            "run",
            "demo",
            *dispatch_args,
            "--issue-id",
            bead_id,
            "--rig-path",
            str(rig),
        ],
    )

    assert result.exit_code == 2, result.output
    assert "dispatch tracker mismatch" in result.output
    assert "No Prefect flow was submitted" in result.output
    flow_called.assert_not_called()
    submit_called.assert_not_called()
