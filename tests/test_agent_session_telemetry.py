"""AgentSession.prompt() telemetry-span integration."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pytest

from prefect_orchestration import telemetry
from prefect_orchestration.agent_session import AgentSession


@pytest.fixture(autouse=True)
def _reset_backend() -> None:
    telemetry.reset_backend()
    yield
    telemetry.reset_backend()


def _otel_inmemory_provider():
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


class _OkBackend:
    def run(
        self,
        prompt: str,
        *,
        session_id: str | None,
        cwd: Path,
        fork: bool = False,
        model: str = "opus",
        effort: str | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> tuple[str, str]:
        return "ok", "sid-after"


class _BoomBackend:
    def run(
        self,
        prompt: str,
        *,
        session_id: str | None,
        cwd: Path,
        fork: bool = False,
        model: str = "opus",
        effort: str | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> tuple[str, str]:
        raise RuntimeError("claude CLI exited 1")


def _make_session(backend, *, issue_id: str | None = "prefect-orchestration-9cn"):
    return AgentSession(
        role="builder",
        repo_path=Path("/tmp"),
        backend=backend,
        session_id="sid-before",
        issue_id=issue_id,
        overlay=False,
        skills=False,
    )


def test_prompt_emits_span_with_required_attrs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("opentelemetry.sdk")
    monkeypatch.setenv("PO_TELEMETRY", "otel")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318/v1/traces")

    _provider, exporter = _otel_inmemory_provider()
    sess = _make_session(_OkBackend())
    out = sess.prompt("hi")
    assert out == "ok"
    assert sess.session_id == "sid-after"
    assert sess._turn_index == 1

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "agent.prompt"
    assert span.attributes["role"] == "builder"
    assert span.attributes["issue_id"] == "prefect-orchestration-9cn"
    assert span.attributes["session_id"] == "sid-before"
    assert span.attributes["turn_index"] == 1
    assert span.attributes["fork_session"] is False
    assert span.attributes["new_session_id"] == "sid-after"


def test_prompt_records_error_status_on_subprocess_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.trace import StatusCode

    monkeypatch.setenv("PO_TELEMETRY", "otel")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318/v1/traces")

    _provider, exporter = _otel_inmemory_provider()
    sess = _make_session(_BoomBackend())
    with pytest.raises(RuntimeError):
        sess.prompt("hi")

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.status.status_code == StatusCode.ERROR
    assert any(ev.name == "exception" for ev in span.events)
    # Turn counter still advances on failure (forensic value).
    assert sess._turn_index == 1


def test_prompt_noop_when_telemetry_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PO_TELEMETRY", raising=False)
    sess = _make_session(_OkBackend())
    assert sess.prompt("hi") == "ok"
    # Telemetry singleton resolved to NoopBackend.
    assert isinstance(telemetry.select_backend(), telemetry.NoopBackend)


def test_prompt_span_nests_under_active_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC 3: agent.prompt nests under the enclosing Prefect task span."""
    pytest.importorskip("opentelemetry.sdk")
    monkeypatch.setenv("PO_TELEMETRY", "otel")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318/v1/traces")

    _provider, exporter = _otel_inmemory_provider()
    from opentelemetry import trace

    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("build-iter-1") as outer:
        outer_id = outer.get_span_context().span_id
        sess = _make_session(_OkBackend())
        sess.prompt("hi")

    by_name = {s.name: s for s in exporter.get_finished_spans()}
    assert by_name["agent.prompt"].parent.span_id == outer_id
