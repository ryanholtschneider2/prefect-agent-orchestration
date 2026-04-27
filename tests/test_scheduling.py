"""Unit tests for `prefect_orchestration.scheduling`.

Pure helper module — no live Prefect server, no real `bd`. The
deployment-lookup / submit functions take a duck-typed `client` so
we exercise them with simple stubs in `test_cli_run_time.py`.

Covers issue prefect-orchestration-7jr ACs §1, §2, §5.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from prefect_orchestration import scheduling


# ─── parse_when: relative durations ──────────────────────────────────


def test_parse_when_hours() -> None:
    spec = scheduling.parse_when("2h")
    assert spec.start_in == timedelta(hours=2)
    assert spec.start_at is None


def test_parse_when_minutes() -> None:
    spec = scheduling.parse_when("30m")
    assert spec.start_in == timedelta(minutes=30)


def test_parse_when_days() -> None:
    spec = scheduling.parse_when("1d")
    assert spec.start_in == timedelta(days=1)


def test_parse_when_weeks() -> None:
    spec = scheduling.parse_when("2w")
    assert spec.start_in == timedelta(weeks=2)


def test_parse_when_seconds() -> None:
    spec = scheduling.parse_when("120s")
    assert spec.start_in == timedelta(seconds=120)


def test_parse_when_plus_prefix() -> None:
    """+30m is the explicit-future variant the issue's design listed."""
    spec = scheduling.parse_when("+30m")
    assert spec.start_in == timedelta(minutes=30)


def test_parse_when_uppercase_unit() -> None:
    spec = scheduling.parse_when("2H")
    assert spec.start_in == timedelta(hours=2)


def test_parse_when_whitespace_inside() -> None:
    """Whitespace between number and unit is forgiven."""
    spec = scheduling.parse_when("2 h")
    assert spec.start_in == timedelta(hours=2)


def test_parse_when_strips_outer_whitespace() -> None:
    spec = scheduling.parse_when("  2h  ")
    assert spec.start_in == timedelta(hours=2)


def test_parse_when_zero_relative_rejected() -> None:
    with pytest.raises(ValueError, match="must be > 0"):
        scheduling.parse_when("0h")


# ─── parse_when: ISO-8601 ────────────────────────────────────────────


def test_parse_when_iso_with_offset() -> None:
    spec = scheduling.parse_when("2026-04-25T09:00:00-04:00")
    assert spec.start_in is None
    # Converted to UTC for storage
    assert spec.start_at == datetime(2026, 4, 25, 13, 0, 0, tzinfo=timezone.utc)


def test_parse_when_iso_z_suffix() -> None:
    spec = scheduling.parse_when("2026-04-25T13:00:00Z")
    assert spec.start_at == datetime(2026, 4, 25, 13, 0, 0, tzinfo=timezone.utc)


def test_parse_when_iso_naive_rejected() -> None:
    """No tz on the ISO string → reject loudly. Stricter than parse_since."""
    with pytest.raises(ValueError, match="timezone"):
        scheduling.parse_when("2026-04-25T09:00:00")


def test_parse_when_iso_utc_offset_zero() -> None:
    spec = scheduling.parse_when("2026-04-25T13:00:00+00:00")
    assert spec.start_at == datetime(2026, 4, 25, 13, 0, 0, tzinfo=timezone.utc)


# ─── parse_when: bad input ───────────────────────────────────────────


def test_parse_when_empty_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        scheduling.parse_when("")


def test_parse_when_whitespace_only_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        scheduling.parse_when("   ")


def test_parse_when_garbage_rejected() -> None:
    with pytest.raises(ValueError, match="bad --time"):
        scheduling.parse_when("yesterday")


def test_parse_when_unknown_unit_rejected() -> None:
    with pytest.raises(ValueError, match="bad --time"):
        scheduling.parse_when("2y")


# ─── WhenSpec.scheduled_time ─────────────────────────────────────────


def test_scheduled_time_returns_start_at_verbatim() -> None:
    dt = datetime(2026, 4, 25, 13, 0, 0, tzinfo=timezone.utc)
    spec = scheduling.WhenSpec(start_at=dt)
    assert spec.scheduled_time() == dt


def test_scheduled_time_relative_is_now_plus_delta() -> None:
    spec = scheduling.WhenSpec(start_in=timedelta(hours=2))
    before = datetime.now(timezone.utc)
    out = spec.scheduled_time()
    after = datetime.now(timezone.utc)
    assert before + timedelta(hours=2) - timedelta(seconds=2) <= out
    assert out <= after + timedelta(hours=2) + timedelta(seconds=2)
    assert out.tzinfo == timezone.utc


# ─── ManualDeploymentMissing message shape (AC §4) ───────────────────


def test_manual_deployment_missing_message_contains_fix() -> None:
    exc = scheduling.ManualDeploymentMissing("software-dev-full")
    msg = str(exc)
    # The error must point at the user's fix (AC §4):
    assert "software-dev-full-manual" in msg
    assert "register" in msg
    assert "po deploy --apply" in msg
    # The Python-identifier hint should snake-case the formula name:
    assert "software_dev_full.to_deployment" in msg
    assert exc.formula == "software-dev-full"


# ─── find_manual_deployment / submit_scheduled_run ───────────────────


class _FakeDeployment:
    def __init__(self, name: str, flow_id: str) -> None:
        self.name = name
        self.flow_id = flow_id


class _FakeFlow:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeClient:
    """Records the kwargs each Prefect-client call receives."""

    def __init__(self, deployments: list[_FakeDeployment], flow: _FakeFlow) -> None:
        self._deployments = deployments
        self._flow = flow
        self.read_deployments_calls: list[dict] = []
        self.read_flow_calls: list[str] = []

    async def read_deployments(self, **kwargs: object) -> list[_FakeDeployment]:
        self.read_deployments_calls.append(kwargs)
        return list(self._deployments)

    async def read_flow(self, flow_id: str) -> _FakeFlow:
        self.read_flow_calls.append(flow_id)
        return self._flow


@pytest.mark.asyncio
async def test_find_manual_deployment_filters_by_name() -> None:
    pytest.importorskip("prefect")
    dep = _FakeDeployment(name="software-dev-full-manual", flow_id="abc")
    client = _FakeClient(deployments=[dep], flow=_FakeFlow("software_dev_full"))
    out = await scheduling.find_manual_deployment(client, "software-dev-full")
    assert out is dep
    # Filter must include exactly the name we expect
    assert client.read_deployments_calls
    df = client.read_deployments_calls[0]["deployment_filter"]
    name_filter = df.name
    assert "software-dev-full-manual" in (name_filter.any_ or [])


@pytest.mark.asyncio
async def test_find_manual_deployment_returns_none_when_empty() -> None:
    pytest.importorskip("prefect")
    client = _FakeClient(deployments=[], flow=_FakeFlow("x"))
    out = await scheduling.find_manual_deployment(client, "software-dev-full")
    assert out is None


@pytest.mark.asyncio
async def test_submit_scheduled_run_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("prefect")
    client = _FakeClient(deployments=[], flow=_FakeFlow("x"))
    with pytest.raises(scheduling.ManualDeploymentMissing) as info:
        await scheduling.submit_scheduled_run(
            client=client,
            formula="software-dev-full",
            parameters={"issue_id": "po-1"},
            when=scheduling.WhenSpec(start_in=timedelta(hours=2)),
        )
    assert info.value.formula == "software-dev-full"


@pytest.mark.asyncio
async def test_submit_scheduled_run_calls_arun_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("prefect")
    dep = _FakeDeployment(name="software-dev-full-manual", flow_id="abc")
    client = _FakeClient(deployments=[dep], flow=_FakeFlow("software_dev_full"))

    captured: dict = {}

    async def _fake_arun_deployment(*args: object, **kwargs: object):
        captured["args"] = args
        captured["kwargs"] = kwargs

        class _FR:
            id = "fr-123"

        return _FR()

    import prefect.deployments.flow_runs as flow_runs_mod

    monkeypatch.setattr(flow_runs_mod, "arun_deployment", _fake_arun_deployment)

    when = scheduling.WhenSpec(start_in=timedelta(hours=2))
    flow_run, full_name = await scheduling.submit_scheduled_run(
        client=client,
        formula="software-dev-full",
        parameters={"issue_id": "po-1"},
        when=when,
        issue_id="po-1",
    )

    assert flow_run.id == "fr-123"
    assert full_name == "software_dev_full/software-dev-full-manual"
    assert captured["args"] == ("software_dev_full/software-dev-full-manual",)
    kw = captured["kwargs"]
    assert kw["parameters"] == {"issue_id": "po-1"}
    assert kw["timeout"] == 0
    assert kw["as_subflow"] is False
    assert kw["tags"] == ["issue_id:po-1"]
    # scheduled_time should be (roughly) now + 2h
    sched: datetime = kw["scheduled_time"]
    delta = sched - datetime.now(timezone.utc)
    assert timedelta(hours=2) - timedelta(seconds=5) <= delta <= timedelta(hours=2)


@pytest.mark.asyncio
async def test_submit_scheduled_run_omits_tags_when_no_issue_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("prefect")
    dep = _FakeDeployment(name="x-manual", flow_id="abc")
    client = _FakeClient(deployments=[dep], flow=_FakeFlow("x"))

    captured: dict = {}

    async def _fake_arun_deployment(*args: object, **kwargs: object):
        captured["kwargs"] = kwargs

        class _FR:
            id = "fr-1"

        return _FR()

    import prefect.deployments.flow_runs as flow_runs_mod

    monkeypatch.setattr(flow_runs_mod, "arun_deployment", _fake_arun_deployment)

    await scheduling.submit_scheduled_run(
        client=client,
        formula="x",
        parameters={},
        when=scheduling.WhenSpec(start_in=timedelta(minutes=1)),
        issue_id=None,
    )
    assert captured["kwargs"]["tags"] is None
