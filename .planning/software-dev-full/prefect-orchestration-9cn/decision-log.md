# Decision log — prefect-orchestration-9cn

- **Decision**: `select_backend()` returns `NoopBackend` when `PO_TELEMETRY`
  is unset, even if `LOGFIRE_TOKEN` is set in env.
  **Why**: Triage flagged "auto-enable when token set" as surprising. Plan §2 codifies opt-in.
  **Alternatives considered**: auto-detecting Logfire from `LOGFIRE_TOKEN` — rejected for least-surprise.

- **Decision**: `OtelBackend` only installs a new `TracerProvider` when the
  current one is `ProxyTracerProvider`; otherwise it just gets a tracer
  off the existing provider.
  **Why**: Prefect 3 (or Logfire) may have already installed a provider; OTel
  warns + silently keeps the first one if you try to replace it. Reusing the
  existing provider keeps spans in one stream.
  **Alternatives considered**: forcing our own provider — would silently drop
  spans the moment Prefect's OTel hook is enabled.

- **Decision**: Span boundary in `AgentSession.prompt()` wraps **only**
  `self.backend.run(...)`. Mail-inject + pack-materialize stay outside.
  **Why**: AC-8 requires span duration within 50ms of subprocess wall time.
  Mail/overlay calls are fast but not relevant to the trace.
  **Alternatives considered**: wrapping the whole `prompt()` body — clearer but
  inflates duration unpredictably and dilutes the per-turn signal.

- **Decision**: Increment `_turn_index` *before* `backend.run`, so failed
  turns still advance the counter.
  **Why**: forensic clarity — when investigating a failure you want
  "turn N failed", not "turn N-1 failed and N never happened".

- **Decision**: `tmux_session` attribute is best-effort and only set for
  non-fork tmux backends. Forked tmux turns randomise a 6-char suffix
  inside `backend.run`; pre-computing it would duplicate logic.
  **Why**: The attribute is observability gravy, not a hard requirement.

- **Decision**: Coerce non-primitive attribute values to `str` in
  `_OtelSpanWrapper.set_attribute`; drop `None` silently.
  **Why**: OTel only accepts primitives (str/bool/int/float). `issue_id` is
  often `None` in tests — silently dropping keeps the API ergonomic.

- **Decision**: Cache `select_backend()` result behind a `threading.Lock`
  module global; expose `reset_backend()` for tests.
  **Why**: SDK init is non-trivial (provider install, exporter wiring); we
  shouldn't redo it per turn or risk concurrent re-init.

- **Decision**: Test helper `_otel_inmemory_provider()` reuses an existing
  global `TracerProvider` when one is already installed and just adds a
  fresh `InMemorySpanExporter` per call.
  **Why**: OTel forbids replacing an installed (non-proxy) provider; the
  first call wins. Adding processors is allowed and gives each test its own
  exporter.
  **Alternatives considered**: forcing a fresh provider per test — produced
  warnings and silently dropped spans.

- **Decision**: Catch `BaseException` (not `Exception`) around `backend.run`
  to record telemetry, then re-raise.
  **Why**: KeyboardInterrupt / SystemExit are still observability signals
  worth capturing on a long-running orchestration.
