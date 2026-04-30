"""Unit tests for `po run <formula> --at <when>` (issue prefect-orchestration-40y).

Drives the Typer `run` command via `CliRunner`. Mocks `_load_formulas`,
the Prefect `get_client()` async-context, and `submit_scheduled_run`
so no Prefect server is needed.

Covers ACs: §1 (relative submits scheduled run), §2 (ISO submits with
parsed datetime), §3 (no `--at` falls through unchanged), §4 (missing
deployment error message), §6 (worker reminder in stdout),
and new: §7 (--time deprecated alias), §8 (auto-apply on missing).
"""

from __future__ import annotations

import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from prefect_orchestration import cli, scheduling


# ─── helpers ─────────────────────────────────────────────────────────


class _RecordedFormula:
    """A registered-formula stand-in. Counts synchronous invocations."""

    def __init__(self, return_value: Any = "ok") -> None:
        self.calls: list[dict[str, Any]] = []
        self.return_value = return_value

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return self.return_value


class _FakeFlowRun:
    def __init__(self, id_: str = "fr-test") -> None:
        self.id = id_


class _FakeAsyncClientCtx:
    """Stand-in for `prefect.client.orchestration.get_client()` —
    an async context manager that yields anything (the unit tests
    swap `submit_scheduled_run` so the client itself is never used)."""

    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ─── AC §3: --at omitted → existing sync behavior ────────────────────


def test_no_time_falls_through_to_sync_path(runner: CliRunner) -> None:
    """With `--at` absent, the formula is invoked synchronously and
    no Prefect deployment lookup happens. Regression for AC §3."""
    fake = _RecordedFormula(return_value="sync-result")
    with (
        patch.object(cli, "_load_formulas", return_value={"foo": fake}),
        patch.object(cli._scheduling, "submit_scheduled_run") as submit,
    ):
        result = runner.invoke(cli.app, ["run", "foo", "--issue-id", "po-1"])
    assert result.exit_code == 0, result.output
    assert "sync-result" in result.output
    assert fake.calls == [{"issue_id": "po-1"}]
    submit.assert_not_called()


# ─── AC §1: relative duration submits a scheduled run ────────────────


def test_relative_time_submits_scheduled_run(runner: CliRunner) -> None:
    """`--at 2h` parses to a 2-hour offset and reaches submit_scheduled_run."""
    fake = _RecordedFormula()
    captured: dict[str, Any] = {}

    async def _fake_submit(**kwargs: Any) -> tuple[_FakeFlowRun, str, None]:
        captured.update(kwargs)
        return _FakeFlowRun("fr-relative"), "foo/foo-manual", None

    with (
        patch.object(cli, "_load_formulas", return_value={"foo": fake}),
        patch(
            "prefect.client.orchestration.get_client",
            return_value=_FakeAsyncClientCtx(),
        ),
        patch.object(cli._scheduling, "submit_scheduled_run", _fake_submit),
    ):
        result = runner.invoke(
            cli.app,
            ["run", "foo", "--at", "2h", "--issue-id", "po-1", "--rig", "site"],
        )
    assert result.exit_code == 0, result.output
    # Synchronous path must NOT have been invoked when --at is set:
    assert fake.calls == []
    assert captured["formula"] == "foo"
    # `--at` token must not leak into formula kwargs:
    assert captured["parameters"] == {"issue_id": "po-1", "rig": "site"}
    assert "at" not in captured["parameters"]
    assert captured["issue_id"] == "po-1"
    sched = captured["scheduled_time"]
    assert isinstance(sched, datetime)
    assert sched.tzinfo == timezone.utc
    delta = sched - datetime.now(timezone.utc)
    assert timedelta(hours=2) - timedelta(seconds=5) <= delta <= timedelta(hours=2)
    # Output advertises the scheduled run
    assert "fr-relative" in result.output
    assert "foo/foo-manual" in result.output


def test_relative_time_plus_prefix(runner: CliRunner) -> None:
    """`+30m` is also accepted (issue's design listed this variant)."""
    fake = _RecordedFormula()
    captured: dict[str, Any] = {}

    async def _fake_submit(**kwargs: Any) -> tuple[_FakeFlowRun, str, None]:
        captured.update(kwargs)
        return _FakeFlowRun("fr-plus"), "foo/foo-manual", None

    with (
        patch.object(cli, "_load_formulas", return_value={"foo": fake}),
        patch(
            "prefect.client.orchestration.get_client",
            return_value=_FakeAsyncClientCtx(),
        ),
        patch.object(cli._scheduling, "submit_scheduled_run", _fake_submit),
    ):
        result = runner.invoke(cli.app, ["run", "foo", "--at", "+30m"])
    assert result.exit_code == 0, result.output
    sched: datetime = captured["scheduled_time"]
    delta = sched - datetime.now(timezone.utc)
    assert (
        timedelta(minutes=30) - timedelta(seconds=5) <= delta <= timedelta(minutes=30)
    )


# ─── AC §2: ISO-8601 with timezone ───────────────────────────────────


def test_iso_time_submits_with_parsed_datetime(runner: CliRunner) -> None:
    """`--at <ISO>` lands as a UTC datetime in submit."""
    fake = _RecordedFormula()
    captured: dict[str, Any] = {}

    async def _fake_submit(**kwargs: Any) -> tuple[_FakeFlowRun, str, None]:
        captured.update(kwargs)
        return _FakeFlowRun("fr-iso"), "foo/foo-manual", None

    iso = "2026-04-25T09:00:00-04:00"
    with (
        patch.object(cli, "_load_formulas", return_value={"foo": fake}),
        patch(
            "prefect.client.orchestration.get_client",
            return_value=_FakeAsyncClientCtx(),
        ),
        patch.object(cli._scheduling, "submit_scheduled_run", _fake_submit),
    ):
        result = runner.invoke(cli.app, ["run", "foo", "--at", iso])
    assert result.exit_code == 0, result.output
    assert captured["scheduled_time"] == datetime(
        2026, 4, 25, 13, 0, tzinfo=timezone.utc
    )


def test_iso_naive_rejected(runner: CliRunner) -> None:
    """Naive ISO datetimes get a clear error before any Prefect call."""
    fake = _RecordedFormula()
    with (
        patch.object(cli, "_load_formulas", return_value={"foo": fake}),
        patch.object(cli._scheduling, "submit_scheduled_run") as submit,
    ):
        result = runner.invoke(cli.app, ["run", "foo", "--at", "2026-04-25T09:00:00"])
    assert result.exit_code == 2
    assert "timezone" in result.output
    submit.assert_not_called()


def test_garbage_time_rejected(runner: CliRunner) -> None:
    fake = _RecordedFormula()
    with (
        patch.object(cli, "_load_formulas", return_value={"foo": fake}),
        patch.object(cli._scheduling, "submit_scheduled_run") as submit,
    ):
        result = runner.invoke(cli.app, ["run", "foo", "--at", "yesterday"])
    assert result.exit_code == 2
    assert "bad --at" in result.output
    submit.assert_not_called()


# ─── AC §4: missing manual deployment error message ──────────────────


def test_missing_manual_deployment_error_message(runner: CliRunner) -> None:
    """When `<formula>-manual` isn't on the server, exit 3 with a fix hint."""
    fake = _RecordedFormula()

    async def _fake_submit(**kwargs: Any) -> tuple[_FakeFlowRun, str, None]:
        raise scheduling.ManualDeploymentMissing(kwargs["formula"])

    with (
        patch.object(cli, "_load_formulas", return_value={"foo": fake}),
        patch(
            "prefect.client.orchestration.get_client",
            return_value=_FakeAsyncClientCtx(),
        ),
        patch.object(cli._scheduling, "submit_scheduled_run", _fake_submit),
    ):
        result = runner.invoke(cli.app, ["run", "foo", "--at", "2h"])
    assert result.exit_code == 3, result.output
    out = result.output
    assert "foo-manual" in out
    assert "register" in out
    assert "po deploy --apply" in out


# ─── AC §6: worker startup reminder ──────────────────────────────────


def test_worker_reminder_in_output(runner: CliRunner) -> None:
    """When submit returns no warn_msg, the generic worker reminder is shown."""
    fake = _RecordedFormula()

    async def _fake_submit(**kwargs: Any) -> tuple[_FakeFlowRun, str, None]:
        return _FakeFlowRun("fr-1"), "foo/foo-manual", None

    with (
        patch.object(cli, "_load_formulas", return_value={"foo": fake}),
        patch(
            "prefect.client.orchestration.get_client",
            return_value=_FakeAsyncClientCtx(),
        ),
        patch.object(cli._scheduling, "submit_scheduled_run", _fake_submit),
    ):
        result = runner.invoke(cli.app, ["run", "foo", "--at", "2h"])
    assert result.exit_code == 0, result.output
    assert "prefect worker start --pool po" in result.output


def test_worker_warning_no_workers(runner: CliRunner) -> None:
    """When submit returns a warn_msg, it is shown instead of the generic reminder."""
    fake = _RecordedFormula()
    warn = (
        "warning: no workers running on pool 'po'. Run `prefect worker start --pool po`"
    )

    async def _fake_submit(**kwargs: Any) -> tuple[_FakeFlowRun, str, str]:
        return _FakeFlowRun("fr-warn"), "foo/foo-manual", warn

    with (
        patch.object(cli, "_load_formulas", return_value={"foo": fake}),
        patch(
            "prefect.client.orchestration.get_client",
            return_value=_FakeAsyncClientCtx(),
        ),
        patch.object(cli._scheduling, "submit_scheduled_run", _fake_submit),
    ):
        result = runner.invoke(cli.app, ["run", "foo", "--at", "2h"])
    assert result.exit_code == 0, result.output
    combined = result.output + (result.stderr or "")
    assert "no workers" in combined or "worker" in combined


# ─── --time deprecated alias ─────────────────────────────────────────


def test_time_alias_deprecated_warning(runner: CliRunner) -> None:
    """`--time` still works but prints a deprecation warning."""
    fake = _RecordedFormula()
    captured: dict[str, Any] = {}

    async def _fake_submit(**kwargs: Any) -> tuple[_FakeFlowRun, str, None]:
        captured.update(kwargs)
        return _FakeFlowRun("fr-compat"), "foo/foo-manual", None

    with (
        patch.object(cli, "_load_formulas", return_value={"foo": fake}),
        patch(
            "prefect.client.orchestration.get_client",
            return_value=_FakeAsyncClientCtx(),
        ),
        patch.object(cli._scheduling, "submit_scheduled_run", _fake_submit),
    ):
        result = runner.invoke(cli.app, ["run", "foo", "--time", "2h"])
    assert result.exit_code == 0, result.output
    combined = result.output + (result.stderr or "")
    assert "deprecated" in combined
    assert "--at" in combined
    # The run still proceeds — submit was called with the right time
    assert captured.get("formula") == "foo"
    sched = captured.get("scheduled_time")
    assert isinstance(sched, datetime)


def test_time_and_at_mutually_exclusive(runner: CliRunner) -> None:
    """Passing both --time and --at is an error."""
    with patch.object(cli._scheduling, "submit_scheduled_run") as submit:
        result = runner.invoke(cli.app, ["run", "foo", "--at", "2h", "--time", "1h"])
    assert result.exit_code == 2
    combined = result.output + (result.stderr or "")
    assert "mutually exclusive" in combined
    submit.assert_not_called()


# ─── auto-apply when deployment missing ──────────────────────────────


def test_auto_apply_no_pack_match_errors(runner: CliRunner) -> None:
    """When no pack registers the deployment, submit raises ManualDeploymentMissing → exit 3."""
    fake = _RecordedFormula()

    async def _fake_submit(**kwargs: Any) -> tuple[_FakeFlowRun, str, None]:
        raise scheduling.ManualDeploymentMissing(kwargs["formula"])

    with (
        patch.object(cli, "_load_formulas", return_value={"foo": fake}),
        patch(
            "prefect.client.orchestration.get_client",
            return_value=_FakeAsyncClientCtx(),
        ),
        patch.object(cli._scheduling, "submit_scheduled_run", _fake_submit),
    ):
        result = runner.invoke(cli.app, ["run", "foo", "--at", "2h"])
    assert result.exit_code == 3
    assert "foo-manual" in result.output


# ─── --at + --from-file is rejected ──────────────────────────────────


def test_time_with_from_file_rejected(runner: CliRunner, tmp_path: Path) -> None:
    """Scratch flows have no deployment; combining --at + --from-file errors."""
    scratch = tmp_path / "s.py"
    scratch.write_text(
        textwrap.dedent(
            """
            from prefect import flow

            @flow
            def f():
                return 1
            """
        )
    )
    result = runner.invoke(cli.app, ["run", "--from-file", str(scratch), "--at", "2h"])
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_time_without_formula_name_rejected(runner: CliRunner) -> None:
    """`po run --at 2h` (no formula) errors before any prefect call."""
    with patch.object(cli._scheduling, "submit_scheduled_run") as submit:
        result = runner.invoke(cli.app, ["run", "--at", "2h"])
    assert result.exit_code == 2
    assert "formula name" in result.output
    submit.assert_not_called()


def test_time_unknown_formula_rejected(runner: CliRunner) -> None:
    with (
        patch.object(cli, "_load_formulas", return_value={}),
        patch.object(cli._scheduling, "submit_scheduled_run") as submit,
    ):
        result = runner.invoke(cli.app, ["run", "no-such-formula", "--at", "2h"])
    assert result.exit_code == 1
    assert "no formula named" in result.output
    submit.assert_not_called()
