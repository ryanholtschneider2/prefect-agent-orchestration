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
#
# Relative durations resolve eagerly to `now() + delta`, so we assert
# the returned datetime falls in a `[before+delta, after+delta]` window
# (with a few seconds of slack for test-runner jitter).


def _assert_relative(out: datetime, delta: timedelta) -> None:
    now = datetime.now(timezone.utc)
    slack = timedelta(seconds=5)
    assert now + delta - slack <= out <= now + delta + slack
    assert out.tzinfo == timezone.utc


def test_parse_when_hours() -> None:
    _assert_relative(scheduling.parse_when("2h"), timedelta(hours=2))


def test_parse_when_minutes() -> None:
    _assert_relative(scheduling.parse_when("30m"), timedelta(minutes=30))


def test_parse_when_days() -> None:
    _assert_relative(scheduling.parse_when("1d"), timedelta(days=1))


def test_parse_when_weeks() -> None:
    _assert_relative(scheduling.parse_when("2w"), timedelta(weeks=2))


def test_parse_when_seconds() -> None:
    _assert_relative(scheduling.parse_when("120s"), timedelta(seconds=120))


def test_parse_when_plus_prefix() -> None:
    """+30m is the explicit-future variant the issue's design listed."""
    _assert_relative(scheduling.parse_when("+30m"), timedelta(minutes=30))


def test_parse_when_uppercase_unit() -> None:
    _assert_relative(scheduling.parse_when("2H"), timedelta(hours=2))


def test_parse_when_whitespace_inside() -> None:
    """Whitespace between number and unit is forgiven."""
    _assert_relative(scheduling.parse_when("2 h"), timedelta(hours=2))


def test_parse_when_strips_outer_whitespace() -> None:
    _assert_relative(scheduling.parse_when("  2h  "), timedelta(hours=2))


def test_parse_when_zero_relative_rejected() -> None:
    with pytest.raises(ValueError, match="must be > 0"):
        scheduling.parse_when("0h")


# ─── parse_when: ISO-8601 ────────────────────────────────────────────


def test_parse_when_iso_with_offset() -> None:
    out = scheduling.parse_when("2026-04-25T09:00:00-04:00")
    assert out == datetime(2026, 4, 25, 13, 0, 0, tzinfo=timezone.utc)


def test_parse_when_iso_z_suffix() -> None:
    out = scheduling.parse_when("2026-04-25T13:00:00Z")
    assert out == datetime(2026, 4, 25, 13, 0, 0, tzinfo=timezone.utc)


def test_parse_when_iso_naive_rejected() -> None:
    """No tz on the ISO string → reject loudly. Stricter than parse_since."""
    with pytest.raises(ValueError, match="timezone"):
        scheduling.parse_when("2026-04-25T09:00:00")


def test_parse_when_iso_utc_offset_zero() -> None:
    out = scheduling.parse_when("2026-04-25T13:00:00+00:00")
    assert out == datetime(2026, 4, 25, 13, 0, 0, tzinfo=timezone.utc)


# ─── parse_when: bad input ───────────────────────────────────────────


def test_parse_when_empty_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        scheduling.parse_when("")


def test_parse_when_whitespace_only_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        scheduling.parse_when("   ")


def test_parse_when_garbage_rejected() -> None:
    with pytest.raises(ValueError, match="bad --at"):
        scheduling.parse_when("yesterday")


def test_parse_when_unknown_unit_rejected() -> None:
    with pytest.raises(ValueError, match="bad --at"):
        scheduling.parse_when("2y")


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
async def test_submit_scheduled_run_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("prefect")
    from prefect_orchestration import deployments as _deployments

    client = _FakeClient(deployments=[], flow=_FakeFlow("x"))
    # Stub both lookup paths so auto-create doesn't fire.
    monkeypatch.setattr(_deployments, "load_deployments", lambda: ([], []))
    monkeypatch.setattr(scheduling, "_load_formula_flow", lambda f: None)
    with pytest.raises(scheduling.ManualDeploymentMissing) as info:
        await scheduling.submit_scheduled_run(
            client=client,
            formula="software-dev-full",
            parameters={"issue_id": "po-1"},
            scheduled_time=datetime.now(timezone.utc) + timedelta(hours=2),
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

    scheduled = datetime.now(timezone.utc) + timedelta(hours=2)
    flow_run, full_name, warn_msg = await scheduling.submit_scheduled_run(
        client=client,
        formula="software-dev-full",
        parameters={"issue_id": "po-1"},
        scheduled_time=scheduled,
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
    assert kw["scheduled_time"] == scheduled


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

    _flow_run, _full_name, _warn = await scheduling.submit_scheduled_run(
        client=client,
        formula="x",
        parameters={},
        scheduled_time=datetime.now(timezone.utc) + timedelta(minutes=1),
        issue_id=None,
    )
    assert captured["kwargs"]["tags"] is None


# ─── ensure_manual_deployment ─────────────────────────────────────────


class _FakeLoadedDeployment:
    """Minimal stand-in for deployments.LoadedDeployment."""

    def __init__(self, name: str) -> None:
        self.deployment = _FakeApplyableDeployment(name)


class _FakeApplyableDeployment:
    def __init__(self, name: str, work_pool_name: str | None = None) -> None:
        self.name = name
        self.work_pool_name = work_pool_name
        self.applied = False

    def apply(self) -> str:  # sync (called via asyncio.to_thread)
        self.applied = True
        return "fake-dep-id"


class _FakeClientWithWorkers(_FakeClient):
    def __init__(
        self,
        deployments: list,
        flow: _FakeFlow,
        workers: list | None = None,
        after_apply_deployments: list | None = None,
    ) -> None:
        super().__init__(deployments, flow)
        self._workers = workers or []
        self._after_apply_deployments = after_apply_deployments
        self._call_count = 0

    async def read_deployments(self, **kwargs: object) -> list:
        self._call_count += 1
        # Second call (after apply) returns different deployments if configured
        if self._after_apply_deployments is not None and self._call_count > 1:
            return list(self._after_apply_deployments)
        return list(self._deployments)

    async def read_workers(self, work_pool_name: str | None = None) -> list:
        return list(self._workers)


@pytest.mark.asyncio
async def test_ensure_manual_deployment_found_returns_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("prefect")
    dep = _FakeDeployment(name="foo-manual", flow_id="abc")
    client = _FakeClientWithWorkers(
        deployments=[dep], flow=_FakeFlow("foo"), workers=[object()]
    )
    result, warn_msg = await scheduling.ensure_manual_deployment(client, "foo")
    assert result is dep
    assert warn_msg is None  # worker found → no warning


@pytest.mark.asyncio
async def test_ensure_manual_deployment_found_warns_no_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("prefect")
    dep = _FakeDeployment(name="foo-manual", flow_id="abc")
    dep.work_pool_name = "my-pool"  # type: ignore[attr-defined]
    client = _FakeClientWithWorkers(
        deployments=[dep], flow=_FakeFlow("foo"), workers=[]
    )
    result, warn_msg = await scheduling.ensure_manual_deployment(client, "foo")
    assert result is dep
    assert warn_msg is not None
    assert "my-pool" in warn_msg
    assert "worker" in warn_msg.lower()


@pytest.mark.asyncio
async def test_ensure_manual_deployment_auto_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("prefect")
    from prefect_orchestration import deployments as _deployments

    applied_dep = _FakeApplyableDeployment("foo-manual")
    loaded = _FakeLoadedDeployment("foo-manual")
    loaded.deployment = applied_dep

    applied_dep_server = _FakeDeployment(name="foo-manual", flow_id="xyz")
    client = _FakeClientWithWorkers(
        deployments=[],  # not found initially
        flow=_FakeFlow("foo"),
        after_apply_deployments=[applied_dep_server],  # found after apply
    )
    monkeypatch.setattr(_deployments, "load_deployments", lambda: ([loaded], []))

    result, warn_msg = await scheduling.ensure_manual_deployment(client, "foo")
    assert result is applied_dep_server
    assert applied_dep.applied


@pytest.mark.asyncio
async def test_ensure_manual_deployment_no_pack_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("prefect")
    from prefect_orchestration import deployments as _deployments

    client = _FakeClientWithWorkers(deployments=[], flow=_FakeFlow("x"))
    monkeypatch.setattr(_deployments, "load_deployments", lambda: ([], []))
    # "no-such-formula" won't be in the EP registry, but stub anyway for clarity.
    monkeypatch.setattr(scheduling, "_load_formula_flow", lambda f: None)

    with pytest.raises(scheduling.ManualDeploymentMissing) as info:
        await scheduling.ensure_manual_deployment(client, "no-such-formula")
    assert info.value.formula == "no-such-formula"


@pytest.mark.asyncio
async def test_ensure_manual_deployment_auto_creates_from_flow_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When pack-declared deployments are absent, fall back to flow.to_deployment()."""
    pytest.importorskip("prefect")
    from prefect_orchestration import deployments as _deployments

    applied_dep = _FakeApplyableDeployment("myflow-manual")

    class _FakeFlowObj:
        def to_deployment(self, name: str) -> _FakeApplyableDeployment:
            applied_dep.name = name
            return applied_dep

    applied_dep_server = _FakeDeployment(name="myflow-manual", flow_id="xyz")

    # Client returns [] until apply_deployment is called, then returns the server dep.
    class _ClientAutoCreate(_FakeClientWithWorkers):
        def __init__(self) -> None:
            super().__init__(deployments=[], flow=_FakeFlow("myflow"))
            self._applied = False

        async def read_deployments(self, **kwargs: object) -> list:
            return [applied_dep_server] if self._applied else []

        async def read_workers(self, work_pool_name: str | None = None) -> list:
            return []

    client = _ClientAutoCreate()

    def _fake_apply(dep: object) -> None:
        dep.__setattr__("applied", True)  # type: ignore[attr-defined]
        client._applied = True

    monkeypatch.setattr(_deployments, "load_deployments", lambda: ([], []))
    monkeypatch.setattr(scheduling, "_load_formula_flow", lambda f: _FakeFlowObj())
    monkeypatch.setattr(_deployments, "apply_deployment", _fake_apply)

    result, warn_msg = await scheduling.ensure_manual_deployment(client, "myflow")
    assert result is applied_dep_server
    assert applied_dep.applied


@pytest.mark.asyncio
async def test_submit_scheduled_run_passes_job_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """job_variables kwarg is forwarded to arun_deployment."""
    pytest.importorskip("prefect")
    dep = _FakeDeployment(name="foo-manual", flow_id="abc")
    client = _FakeClient(deployments=[dep], flow=_FakeFlow("foo"))

    captured: dict = {}

    async def _fake_arun_deployment(*args: object, **kwargs: object):
        captured["kwargs"] = kwargs

        class _FR:
            id = "fr-jv"

        return _FR()

    import prefect.deployments.flow_runs as flow_runs_mod

    monkeypatch.setattr(flow_runs_mod, "arun_deployment", _fake_arun_deployment)

    await scheduling.submit_scheduled_run(
        client=client,
        formula="foo",
        parameters={},
        scheduled_time=datetime.now(timezone.utc) + timedelta(minutes=1),
        job_variables={"env": {"PO_RESUME": "1"}},
    )
    assert captured["kwargs"]["job_variables"] == {"env": {"PO_RESUME": "1"}}
