"""Optional OpenTelemetry / Logfire instrumentation for AgentSession.

Backends (`NoopBackend`, `OtelBackend`, `LogfireBackend`) implement a
tiny `TelemetryBackend` Protocol — one method, `span(name, **attrs)`,
returning a context manager whose target exposes `set_attribute`,
`record_exception`, and `set_status`.

Selection happens via `PO_TELEMETRY` env var (`none` / `logfire` / `otel`);
default is `none`. SDK imports are lazy — when telemetry is unset, no
opentelemetry/logfire modules are imported at runtime, so the optional
extras stay optional.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Any, ContextManager, Protocol


class _SpanLike(Protocol):
    def set_attribute(self, key: str, value: Any) -> None: ...
    def record_exception(self, exc: BaseException) -> None: ...
    def set_status(self, status: str, description: str | None = None) -> None: ...


class TelemetryBackend(Protocol):
    """Minimal contract: emit a span around a block of work."""

    def span(self, name: str, **attrs: Any) -> ContextManager[_SpanLike]: ...


class _NoopSpan:
    def set_attribute(self, key: str, value: Any) -> None:  # noqa: D401
        return None

    def record_exception(self, exc: BaseException) -> None:
        return None

    def set_status(self, status: str, description: str | None = None) -> None:
        return None


class NoopBackend:
    """Default backend: no-ops, zero deps, zero allocations beyond a sentinel."""

    _SPAN = _NoopSpan()

    @contextmanager
    def span(self, name: str, **attrs: Any):
        yield self._SPAN


class _OtelSpanWrapper:
    """Adapt an OTel `Span` to the `_SpanLike` Protocol."""

    def __init__(self, otel_span: Any) -> None:
        self._span = otel_span

    def set_attribute(self, key: str, value: Any) -> None:
        if value is None:
            return
        # OTel attribute values must be primitives; coerce defensively.
        if not isinstance(value, (str, bool, int, float)):
            value = str(value)
        self._span.set_attribute(key, value)

    def record_exception(self, exc: BaseException) -> None:
        self._span.record_exception(exc)

    def set_status(self, status: str, description: str | None = None) -> None:
        from opentelemetry.trace import Status, StatusCode

        code = {
            "OK": StatusCode.OK,
            "ERROR": StatusCode.ERROR,
            "UNSET": StatusCode.UNSET,
        }.get(status.upper(), StatusCode.UNSET)
        self._span.set_status(Status(code, description=description))


class OtelBackend:
    """Generic OTLP/HTTP backend.

    Idempotent provider install: if a non-default `TracerProvider` is
    already registered (e.g. by Prefect or by `LogfireBackend`), we
    just grab a tracer from it. Spans nest under any active parent
    via OTel context propagation, so an `agent.prompt` span emitted
    from inside a Prefect `@task` automatically becomes a child of
    the task's span.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        headers: dict[str, str] | None = None,
        service_name: str = "prefect-orchestration",
    ) -> None:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.trace import ProxyTracerProvider

        provider = trace.get_tracer_provider()
        if isinstance(provider, ProxyTracerProvider):
            new_provider = TracerProvider()
            self._install_exporter(new_provider, endpoint, headers)
            trace.set_tracer_provider(new_provider)
            provider = new_provider
        self._tracer = trace.get_tracer(service_name)

    @staticmethod
    def _install_exporter(
        provider: Any,
        endpoint: str | None,
        headers: dict[str, str] | None,
    ) -> None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        kwargs: dict[str, Any] = {}
        if endpoint:
            kwargs["endpoint"] = endpoint
        if headers:
            kwargs["headers"] = headers
        exporter = OTLPSpanExporter(**kwargs)
        provider.add_span_processor(BatchSpanProcessor(exporter))

    @contextmanager
    def span(self, name: str, **attrs: Any):
        with self._tracer.start_as_current_span(name) as raw:
            wrapper = _OtelSpanWrapper(raw)
            for key, value in attrs.items():
                wrapper.set_attribute(key, value)
            yield wrapper


class LogfireBackend:
    """Pydantic Logfire backend — auto-configured from `LOGFIRE_TOKEN`.

    Logfire installs itself into OTel's global tracer, so spans nest
    under any active parent the same way `OtelBackend`'s do.
    """

    def __init__(self, token: str | None = None) -> None:
        import logfire

        token = token or os.environ.get("LOGFIRE_TOKEN")
        logfire.configure(token=token)
        self._logfire = logfire

    @contextmanager
    def span(self, name: str, **attrs: Any):
        with self._logfire.span(name, **attrs) as lf_span:
            wrapper = _LogfireSpanWrapper(lf_span)
            yield wrapper


class _LogfireSpanWrapper:
    def __init__(self, lf_span: Any) -> None:
        self._span = lf_span

    def set_attribute(self, key: str, value: Any) -> None:
        if value is None:
            return
        # Logfire spans expose `set_attribute` (OTel-compatible).
        try:
            self._span.set_attribute(key, value)
        except Exception:
            pass

    def record_exception(self, exc: BaseException) -> None:
        try:
            self._span.record_exception(exc)
        except Exception:
            pass

    def set_status(self, status: str, description: str | None = None) -> None:
        try:
            from opentelemetry.trace import Status, StatusCode

            code = {
                "OK": StatusCode.OK,
                "ERROR": StatusCode.ERROR,
                "UNSET": StatusCode.UNSET,
            }.get(status.upper(), StatusCode.UNSET)
            self._span.set_status(Status(code, description=description))
        except Exception:
            pass


_BACKEND: TelemetryBackend | None = None
_BACKEND_LOCK = threading.Lock()


def select_backend() -> TelemetryBackend:
    """Return a process-singleton `TelemetryBackend` per `PO_TELEMETRY`.

    Selection:

    * unset / `none` → `NoopBackend` (no SDK imports)
    * `logfire`      → `LogfireBackend` (requires `LOGFIRE_TOKEN`)
    * `otel`         → `OtelBackend` (requires `OTEL_EXPORTER_OTLP_ENDPOINT`)

    Raises `RuntimeError` for an unknown value or missing prerequisites.
    """
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    with _BACKEND_LOCK:
        if _BACKEND is not None:
            return _BACKEND
        _BACKEND = _build_backend()
        return _BACKEND


def reset_backend() -> None:
    """Clear the cached backend. Intended for tests only."""
    global _BACKEND
    with _BACKEND_LOCK:
        _BACKEND = None


def _build_backend() -> TelemetryBackend:
    raw = (os.environ.get("PO_TELEMETRY") or "none").strip().lower()
    if raw in ("", "none", "noop", "off", "disabled"):
        return NoopBackend()
    if raw == "logfire":
        if not os.environ.get("LOGFIRE_TOKEN"):
            raise RuntimeError(
                "PO_TELEMETRY=logfire requires LOGFIRE_TOKEN in the environment"
            )
        try:
            return LogfireBackend()
        except ImportError as e:
            raise RuntimeError(
                "PO_TELEMETRY=logfire but `logfire` is not installed; "
                "`pip install prefect-orchestration[logfire]`"
            ) from e
    if raw == "otel":
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        if not endpoint:
            raise RuntimeError(
                "PO_TELEMETRY=otel requires OTEL_EXPORTER_OTLP_ENDPOINT in the environment"
            )
        headers_raw = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS")
        headers = _parse_otlp_headers(headers_raw) if headers_raw else None
        try:
            return OtelBackend(endpoint=endpoint, headers=headers)
        except ImportError as e:
            raise RuntimeError(
                "PO_TELEMETRY=otel but the OpenTelemetry SDK is not installed; "
                "`pip install prefect-orchestration[otel]`"
            ) from e
    raise RuntimeError(
        f"PO_TELEMETRY={raw!r} is not a recognised backend "
        "(expected one of: none, logfire, otel)"
    )


def _parse_otlp_headers(raw: str) -> dict[str, str]:
    """Parse the W3C-style header string used by `OTEL_EXPORTER_OTLP_HEADERS`."""
    out: dict[str, str] = {}
    for chunk in raw.split(","):
        if "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        out[k.strip()] = v.strip()
    return out
