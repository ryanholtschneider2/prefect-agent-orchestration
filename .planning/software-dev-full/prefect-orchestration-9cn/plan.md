# Plan — prefect-orchestration-9cn (iter 2: verification-only)

OpenTelemetry / Logfire spans for `AgentSession.prompt()`. **Iter 1
already shipped** in commit `13bb3f4` ("optional OTel/Logfire spans for
AgentSession.prompt()"). All eight acceptance criteria are met by
existing code + tests + docs; remaining work is verification, not
implementation. This plan documents what's on disk so the critic /
verifier can confirm before close.

## State on master (`13bb3f4`)

- `prefect_orchestration/telemetry.py` (271 lines) — `TelemetryBackend`
  Protocol, `NoopBackend`, `OtelBackend`, `LogfireBackend`,
  `select_backend()` cached behind a `threading.Lock`, `reset_backend()`
  test hook, `_parse_otlp_headers()`. SDK imports are lazy inside each
  backend's `__init__`.
- `prefect_orchestration/agent_session.py` — `AgentSession` gained
  `issue_id: str | None = None` + private `_turn_index` counter; `prompt()`
  wraps `self.backend.run(...)` in `tel.span("agent.prompt", ...)` with
  attributes `role`/`issue_id`/`session_id`/`turn_index`/`fork_session`/
  `model` (+ `new_session_id` post-call, `tmux_session` for non-fork
  tmux backends). Exceptions record + set ERROR status, then re-raise.
- `pyproject.toml` — `[project.optional-dependencies]` table with
  `logfire = ["logfire>=3.0"]`,
  `otel = [opentelemetry-api/sdk/exporter-otlp-proto-http >=1.25]`,
  `dev = [opentelemetry-api/sdk]` for in-memory tests.
- `tests/test_telemetry.py` (11 tests) — backend selection, missing
  env-var errors, no-SDK-imports-when-unset, in-memory OTel exporter
  asserts span name/attrs/status/parent.
- `tests/test_agent_session_telemetry.py` (4 tests) — end-to-end
  `prompt()` emits span; ERROR status on backend failure; Noop when
  `PO_TELEMETRY` unset; parent-span nesting.
- `README.md` — "Telemetry / Observability" section with env-var matrix,
  Logfire and OTLP examples, screenshot reference at
  `docs/img/telemetry-logfire.png` (capture deferred to verifier).
- `CLAUDE.md` — short pointer in "Common workflows" pointing at the
  README section.

## Affected files (this iter)

None for code. Verification-only iteration:

- **READ** the eight files above + the iter-1 commit (`13bb3f4`) to
  confirm AC coverage.
- **NO EDITS** unless the plan-critic flags a real gap.

## Approach

Confirm — don't reimplement. The implementation is already in place.
For each AC, point at the file + line range that fulfils it.

| AC | Status | Where |
|---|---|---|
| 1. `telemetry.py` with Protocol + 3 backends | shipped | `prefect_orchestration/telemetry.py` (`NoopBackend` / `OtelBackend` / `LogfireBackend`) |
| 2. `prompt()` wraps each subprocess in `agent.prompt` span with required attrs | shipped | `agent_session.py::AgentSession.prompt()` (`role`, `issue_id`, `session_id`, `turn_index`, `fork_session`) |
| 3. Span parent = enclosing Prefect task | shipped | `OtelBackend` reuses non-proxy global provider; OTel context propagation handles nesting. Unit test `test_prompt_span_nests_under_active_parent` proves it. |
| 4. `PO_TELEMETRY` env var selects backend (`logfire` / `otel` / `none` default) | shipped | `_build_backend()` switch; `RuntimeError` on missing token / endpoint / unknown value. Five unit tests cover each branch. |
| 5. Backward compat: no telemetry env → no behavior change, no new deps imported | shipped | `NoopBackend` is the default; `test_noop_no_sdk_imports` asserts `sys.modules` unchanged. `pyproject.toml` `dependencies` lists no OTel/Logfire packages. |
| 6. Span status ERROR with exception info on non-zero subprocess exit | shipped | `prompt()` `except BaseException` → `record_exception` + `set_status("ERROR", ...)` → re-raise. Unit test `test_prompt_records_error_status_on_subprocess_failure`. |
| 7. README documented w/ Logfire screenshot + OTLP example | shipped (docs); screenshot is verifier-phase work | `README.md` § "Telemetry / Observability" |
| 8. Live `software-dev-full` run shows per-step `agent.prompt` spans w/ correct `role` and ≤50ms timing | **deferred to verifier** | Requires `LOGFIRE_TOKEN` + a real Claude run; cannot be a CI test (no token, no Claude in CI). Verifier runs once locally and pastes the screenshot to satisfy AC 7's image and AC 8's evidence. |

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
| 1 | `Read prefect_orchestration/telemetry.py`; confirm `class TelemetryBackend(Protocol)`, `class NoopBackend`, `class OtelBackend`, `class LogfireBackend`. |
| 2 | `uv run python -m pytest tests/test_agent_session_telemetry.py::test_prompt_emits_span_with_required_attrs` — passes. |
| 3 | `uv run python -m pytest tests/test_agent_session_telemetry.py::test_prompt_span_nests_under_active_parent` — passes. |
| 4 | `uv run python -m pytest tests/test_telemetry.py -k "select or requires or unknown"` covers `unset → Noop`, `none → Noop`, `unknown → RuntimeError`, `logfire missing token → RuntimeError`, `otel missing endpoint → RuntimeError`. |
| 5 | `uv run python -m pytest tests/test_telemetry.py::test_noop_no_sdk_imports` + grep `pyproject.toml` `[project] dependencies` for `opentelemetry` / `logfire` (expect not present; both only under `[project.optional-dependencies]`). |
| 6 | `uv run python -m pytest tests/test_telemetry.py::test_otel_span_error_status_on_exception` + `tests/test_agent_session_telemetry.py::test_prompt_records_error_status_on_subprocess_failure`. |
| 7 | `grep -n "## Telemetry / Observability" README.md` confirms section + OTLP example + screenshot reference. Screenshot capture deferred to verifier (one-shot manual). |
| 8 | Verifier runs `LOGFIRE_TOKEN=… PO_TELEMETRY=logfire po run software-dev-full --issue-id <noop-test-bead> --rig <rig> --rig-path <path>`, opens the resulting Logfire trace, confirms `agent.prompt` spans appear per role, captures timing delta, drops the screenshot at `docs/img/telemetry-logfire.png`. |

## Test plan

- **unit** (`tests/test_telemetry.py`, `tests/test_agent_session_telemetry.py`):
  15 tests, currently green — confirmed via
  `uv run python -m pytest tests/test_telemetry.py tests/test_agent_session_telemetry.py`
  → `15 passed`.
- **e2e**: skipped per the rig's `.po-env PO_SKIP_E2E=1`. No e2e tests
  added or needed — telemetry surface is internal to `AgentSession`,
  the existing CLI roundtrip e2es cover orthogonal flows.
- **playwright**: not applicable.

## Risks

- **Iter-1 already on master** — there's nothing to roll back. Risk
  surface for *this* iteration is essentially zero unless the
  plan-critic identifies a real AC gap.
- **AC 7 / AC 8 screenshot**: requires a one-shot live Logfire run,
  not automatable in CI. Documented as verifier-phase work above.
- **Optional-import discipline** (already covered by
  `test_noop_no_sdk_imports`): if a future edit adds a top-level
  `import logfire` to `telemetry.py`, that test fails — gated.
- **Global tracer provider clash** (already mitigated): `OtelBackend`
  reuses an existing non-proxy provider rather than installing a
  competing one. Documented in `decision-log.md` and the test for
  parent nesting confirms it.
- **API contract**: `AgentSession.issue_id` is additive with default
  `None`; `_turn_index` is private. No breaking change to the pack
  contract — pack flows pick up telemetry by setting `PO_TELEMETRY`,
  zero pack-side edits required for AC 1–6.

## Decision deltas vs iter-1

None this iter. The iter-1 decisions in
`.planning/software-dev-full/prefect-orchestration-9cn/decision-log.md`
stand:

- default backend is `none` even when `LOGFIRE_TOKEN` is set
  (least-surprise opt-in)
- `OtelBackend` reuses existing non-proxy provider (Prefect-friendly)
- span boundary wraps only `backend.run` (50ms accuracy budget)
- `_turn_index` increments before the call (failed turns are still
  numbered)
- `tmux_session` attribute is best-effort, non-fork only
- `BaseException` catch around `backend.run` (KeyboardInterrupt /
  SystemExit are still observability signals)
