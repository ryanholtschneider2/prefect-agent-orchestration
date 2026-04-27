# Plan — prefect-orchestration-9cn

OpenTelemetry / Logfire spans for `AgentSession.prompt()`.

## Affected files

- **NEW** `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/telemetry.py`
  — `TelemetryBackend` Protocol, `NoopBackend`, `OtelBackend`, `LogfireBackend`,
  module-level `select_backend()` that reads `PO_TELEMETRY` and lazy-imports
  the SDK only for the chosen backend. Returns a process-singleton
  (initialised on first call; safe to call from each `AgentSession`).
- **EDIT** `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/agent_session.py`
  — add optional `issue_id: str | None = None` and `_turn_index: int = 0`
  fields on `AgentSession`; wrap the `self.backend.run(...)` call in
  `prompt()` with `telemetry.span("agent.prompt", ...)`. Set status ERROR
  with exception info if `backend.run` raises (let exception propagate
  after recording). Increment `_turn_index` per call regardless of
  outcome. Optionally attach `tmux_session` attribute when the backend
  is a `TmuxClaudeBackend` / `TmuxInteractiveClaudeBackend` (read its
  `_session_name()`).
- **EDIT** `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/pyproject.toml`
  — add `[project.optional-dependencies]` table:
  - `logfire = ["logfire>=3.0"]`
  - `otel = ["opentelemetry-api>=1.25", "opentelemetry-sdk>=1.25", "opentelemetry-exporter-otlp-proto-http>=1.25"]`
  Plus a `dev` extra (or `tests`) pulling `opentelemetry-sdk` so unit
  tests can use the in-memory exporter without a network.
- **NEW** `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_telemetry.py`
  — unit tests for backend selection + Noop no-op + in-memory OTel
  exporter assertions on span name/attributes/status.
- **EDIT** `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_agent_session_overlay.py`
  (or new `tests/test_agent_session_telemetry.py`) — assert `prompt()`
  produces a span via the test backend with `role`, `issue_id`,
  `session_id`, `turn_index`, `fork_session` attributes; assert ERROR
  status when the backend raises.
- **EDIT** `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/README.md`
  — new "Telemetry / Observability" section: env-var matrix
  (`PO_TELEMETRY`, `LOGFIRE_TOKEN`, `OTEL_EXPORTER_OTLP_ENDPOINT`,
  `OTEL_EXPORTER_OTLP_HEADERS`), install commands for the extras, a
  Logfire trace screenshot, and an OTLP/Tempo example. Screenshot
  lives at `docs/img/telemetry-logfire.png` (created during
  verification phase by capturing a real run).
- **EDIT** `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/CLAUDE.md`
  — short note in the "Common workflows" section pointing at the new
  README section.

## Approach

1. `telemetry.py` defines a tiny `TelemetryBackend` Protocol with one
   method: `span(name: str, **attrs) -> ContextManager[SpanLike]` where
   `SpanLike` exposes `set_attribute`, `record_exception`, and
   `set_status`. Implementations:
   - `NoopBackend.span` returns a `contextlib.nullcontext` wrapping a
     dummy span object whose methods are no-ops. **No imports of
     `opentelemetry` or `logfire` at module top level.**
   - `OtelBackend.__init__` lazy-imports `opentelemetry.trace`,
     configures a `TracerProvider` with an OTLP/HTTP exporter from
     env (idempotent: skip if a global provider is already set —
     Prefect 3 may have installed one). `span()` calls
     `tracer.start_as_current_span()` so nesting under any active
     parent span (Prefect's task span when we're inside a `@task`)
     is automatic via OTel's context propagation.
   - `LogfireBackend` lazy-imports `logfire`, calls
     `logfire.configure(token=os.environ["LOGFIRE_TOKEN"])` once,
     then delegates `span()` to `logfire.span(name, **attrs)`.
     Logfire wires itself into OTel's global tracer, so parent-context
     nesting Just Works.
2. Selection logic in `select_backend()`:
   - `PO_TELEMETRY=none` (or unset) → `NoopBackend`. **Default is
     `none` even when `LOGFIRE_TOKEN` is set** (per triage risk on
     surprising auto-enable).
   - `PO_TELEMETRY=logfire` → require `LOGFIRE_TOKEN`; raise a clear
     `RuntimeError` if missing or `logfire` not installed.
   - `PO_TELEMETRY=otel` → require `OTEL_EXPORTER_OTLP_ENDPOINT`;
     raise if missing or OTel SDK not installed.
   - Cached in a module global so repeated `select_backend()` calls
     don't reconfigure the tracer provider.
3. `AgentSession.prompt()` change is small:
   ```python
   tel = telemetry.select_backend()
   self._turn_index += 1
   attrs = {
       "role": self.role,
       "issue_id": self.issue_id,
       "session_id": self.session_id,
       "turn_index": self._turn_index,
       "fork_session": fork,
       "model": self.model,
   }
   with tel.span("agent.prompt", **attrs) as span:
       try:
           result, new_sid = self.backend.run(...)
       except Exception as e:
           span.record_exception(e)
           span.set_status("ERROR", str(e))
           raise
       span.set_attribute("new_session_id", new_sid)
   ```
   Span boundary wraps **only** `backend.run` so duration matches
   subprocess wall time within the AC-8 50ms budget. Mail fetch /
   mark-read happen outside the span (they're fast and not the
   subject of the trace).
4. `issue_id` propagation: callers (the software-dev pack flows)
   already know the issue id — they construct `AgentSession`. Add
   `issue_id` as an optional kwarg with default `None`. The pack
   doesn't have to be edited in this issue (out of scope; the
   attribute will simply be `None` for in-tree tests until the pack
   passes it through). Note this in the docs.
5. Prefect parent-span nesting: rely on OTel context propagation —
   if Prefect has its own tracer provider (Prefect 3 supports OTel
   when configured), the child span automatically nests. We do NOT
   install a competing global tracer provider when one already
   exists; `OtelBackend` checks `trace.get_tracer_provider()` for
   the default `ProxyTracerProvider` before installing.

## Acceptance criteria (verbatim)

1. New `prefect_orchestration/telemetry.py` module with `TelemetryBackend`
   Protocol + `NoopBackend` + `OtelBackend` + `LogfireBackend` impls.
2. `AgentSession.prompt()` wraps each subprocess call in a span named
   `agent.prompt` with attributes
   `{role, issue_id, session_id, turn_index, fork_session}`.
3. Span parent is the enclosing Prefect task run when inside a flow —
   verified by opening a Logfire trace and seeing `agent.prompt`
   nested under e.g. `build-iter-1`.
4. `PO_TELEMETRY` env var selects backend: `logfire` (requires
   `LOGFIRE_TOKEN`), `otel` (requires `OTEL_EXPORTER_OTLP_ENDPOINT`),
   `none` (default, Noop).
5. Backward compat: no telemetry env → no change in behavior, no new
   deps imported at runtime. OTel/Logfire deps are optional extras:
   `pip install prefect-orchestration[logfire]` or `[otel]`.
6. Span status ERROR with exception info on non-zero subprocess exit.
7. Documented in README with a Logfire screenshot and an OTLP example.
8. A live run of `software-dev-full` on a test issue shows every
   step's `agent.prompt` span in Logfire, with correct `role`
   attribute and timing within 50ms of subprocess wall time.

## Verification strategy

| AC | How verified |
|---|---|
| 1 | File exists; class names asserted by import in `tests/test_telemetry.py`. |
| 2 | Unit test installs an in-memory OTel `InMemorySpanExporter`, sets `PO_TELEMETRY=otel` + endpoint stub, runs `AgentSession(...).prompt()` against `StubBackend`, asserts emitted span name `agent.prompt` and the five required attributes are present and equal expected values. |
| 3 | Unit test starts an outer span, runs `prompt()` inside, asserts the recorded `agent.prompt` span's `parent_span_id` equals the outer span's `span_id`. (Real Logfire screenshot belongs to AC 8.) |
| 4 | `tests/test_telemetry.py::test_select_backend_*` cases for each value of `PO_TELEMETRY` (`unset`, `none`, `logfire`, `otel`, invalid). Missing-token cases assert `RuntimeError`. |
| 5 | Test that with `PO_TELEMETRY` unset, `sys.modules` does not contain `logfire` or `opentelemetry` after a `prompt()` call. Also: `pip install prefect-orchestration` (no extras) succeeds without pulling OTel/Logfire — covered by checking pyproject `dependencies` doesn't list them. |
| 6 | Unit test: backend whose `run` raises → assert recorded span has `status=ERROR` and `exception` event. |
| 7 | README diff includes the new section + a `docs/img/telemetry-logfire.png` reference. Verifier (later phase) runs once with `PO_TELEMETRY=logfire` and pastes the screenshot. |
| 8 | Live `po run software-dev-full` on a no-op test issue with `PO_TELEMETRY=logfire`; Logfire UI shows N spans (one per role turn) with correct `role` attribute. Wall-time delta computed by recording `t0/t1` around `backend.run` in a debug log line and comparing to span duration in Logfire export — assert `<50ms`. Done in verification phase. |

## Test plan

- **unit** (`tests/test_telemetry.py`, `tests/test_agent_session_telemetry.py`):
  selection logic, Noop no-op, in-memory OTel exporter assertions on
  span name/attrs/status/parent. Runs with `opentelemetry-sdk` from
  the dev extra. **No network, no Logfire calls.**
- **e2e** (none required) — AC 8 is a manual / verifier-phase live run
  against Logfire; not a CI test (would require a real token in CI,
  which we don't want).
- **playwright**: not applicable.

## Risks

- **Global tracer provider clash**: Prefect 3 may install its own
  provider; reinstalling ours would silently drop spans. Mitigation:
  `OtelBackend` only installs a provider if the current one is
  `ProxyTracerProvider`; otherwise it just gets a tracer from the
  existing provider.
- **AC-8 50ms accuracy**: easy if span boundary is tight around
  `backend.run`. Risk if mail-inject or pack-materialize is moved
  inside; plan keeps them outside.
- **Optional-import discipline**: a stray top-level `import logfire`
  in `telemetry.py` would break AC 5. Add a unit test that asserts
  `logfire` and `opentelemetry` are absent from `sys.modules`
  after `import prefect_orchestration.telemetry` when `PO_TELEMETRY`
  is unset.
- **API contract for `AgentSession`**: adding `issue_id` and
  `_turn_index` is additive and defaults to None/0. No breaking
  change. Existing tests pass without modification.
- **No migration / no API contract changes** otherwise.
- **Concurrent flows**: `select_backend()` cache must be
  thread/process-safe. Use a `threading.Lock` around first-time
  init; OTel SDK itself is fine to call concurrently after.
