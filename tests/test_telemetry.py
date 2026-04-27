"""Unit tests for the optional telemetry backend selection."""

from __future__ import annotations

import sys

import pytest

from prefect_orchestration import telemetry


@pytest.fixture(autouse=True)
def _reset_backend() -> None:
    telemetry.reset_backend()
    yield
    telemetry.reset_backend()


def test_default_is_noop_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PO_TELEMETRY", raising=False)
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    backend = telemetry.select_backend()
    assert isinstance(backend, telemetry.NoopBackend)


def test_explicit_none_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PO_TELEMETRY", "none")
    assert isinstance(telemetry.select_backend(), telemetry.NoopBackend)


def test_unknown_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PO_TELEMETRY", "honeycomb-or-something")
    with pytest.raises(RuntimeError, match="not a recognised backend"):
        telemetry.select_backend()


def test_logfire_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PO_TELEMETRY", "logfire")
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="LOGFIRE_TOKEN"):
        telemetry.select_backend()


def test_otel_requires_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PO_TELEMETRY", "otel")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    with pytest.raises(RuntimeError, match="OTEL_EXPORTER_OTLP_ENDPOINT"):
        telemetry.select_backend()


def test_noop_no_sdk_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC 5: unset env → no opentelemetry/logfire imports during span()."""
    monkeypatch.delenv("PO_TELEMETRY", raising=False)
    # Pretend they were never imported. Don't blow away modules already
    # imported by other tests; just record the delta.
    before_ot = "opentelemetry" in sys.modules
    before_lf = "logfire" in sys.modules
    backend = telemetry.select_backend()
    with backend.span("noop", role="x", issue_id="y") as span:
        span.set_attribute("k", "v")
        span.set_status("OK")
    after_ot = "opentelemetry" in sys.modules
    after_lf = "logfire" in sys.modules
    # NoopBackend must not introduce new SDK imports.
    assert after_ot == before_ot
    assert after_lf == before_lf


def test_noop_span_swallows_set_status_and_record() -> None:
    backend = telemetry.NoopBackend()
    with backend.span("x", role="r") as span:
        span.set_attribute("k", 1)
        span.record_exception(RuntimeError("boom"))
        span.set_status("ERROR", "boom")


def test_parse_otlp_headers() -> None:
    out = telemetry._parse_otlp_headers("a=1,b=two,c= padded ")
    assert out == {"a": "1", "b": "two", "c": "padded"}


# --- OTel-backed assertions (require opentelemetry-sdk in dev extras) ---


def _otel_inmemory_provider():
    """Install a TracerProvider with a fresh in-memory exporter.

    OTel forbids replacing an already-installed (non-proxy) TracerProvider
    in-process — `set_tracer_provider` silently keeps the first one. We
    work with that: install once, then add a *new* exporter on each call
    so each test sees only its own spans.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from opentelemetry.trace import ProxyTracerProvider

    provider = trace.get_tracer_provider()
    if isinstance(provider, ProxyTracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def test_otel_span_records_attributes_and_status() -> None:
    pytest.importorskip("opentelemetry.sdk")
    _provider, exporter = _otel_inmemory_provider()
    backend = telemetry.OtelBackend()  # picks up the just-installed provider

    with backend.span(
        "agent.prompt",
        role="builder",
        issue_id="prefect-orchestration-9cn",
        session_id="sid-1",
        turn_index=1,
        fork_session=False,
    ) as span:
        span.set_attribute("new_session_id", "sid-2")

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    s = spans[0]
    assert s.name == "agent.prompt"
    assert s.attributes["role"] == "builder"
    assert s.attributes["issue_id"] == "prefect-orchestration-9cn"
    assert s.attributes["turn_index"] == 1
    assert s.attributes["fork_session"] is False
    assert s.attributes["new_session_id"] == "sid-2"


def test_otel_span_error_status_on_exception() -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.trace import StatusCode

    _provider, exporter = _otel_inmemory_provider()
    backend = telemetry.OtelBackend()

    with pytest.raises(RuntimeError):
        with backend.span("agent.prompt", role="r") as span:
            try:
                raise RuntimeError("subprocess exited 1")
            except RuntimeError as e:
                span.record_exception(e)
                span.set_status("ERROR", str(e))
                raise

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code == StatusCode.ERROR
    # OTel records exceptions as events.
    assert any(ev.name == "exception" for ev in spans[0].events)


def test_otel_span_nests_under_active_parent() -> None:
    pytest.importorskip("opentelemetry.sdk")
    _provider, exporter = _otel_inmemory_provider()
    backend = telemetry.OtelBackend()

    from opentelemetry import trace

    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("outer") as outer:
        outer_ctx = outer.get_span_context()
        with backend.span("agent.prompt", role="r"):
            pass

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert "agent.prompt" in spans and "outer" in spans
    assert spans["agent.prompt"].parent.span_id == outer_ctx.span_id
