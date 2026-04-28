# Decision log — `prefect-orchestration-ddh` (per-agent secret injection)

## Iter 1 (build)

- **Decision**: Land `SecretProvider` Protocol in core
  (`prefect_orchestration/secrets.py`), with `EnvSecretProvider` +
  `DotenvSecretProvider` + `ChainSecretProvider` impls; wire into
  `AgentSession` via a single `secret_provider` field; resolve
  per-role env once per turn in `prompt()` and pass as
  `extra_env=` to `SessionBackend.run()`.
  **Why**: The backend launch boundary is the only place where the
  child Claude subprocess is constructed, and it's already the
  natural seam for per-call env overrides. Doing the injection
  there keeps `AgentSession` from owning subprocess wiring and
  keeps every backend (`ClaudeCliBackend`, `TmuxClaudeBackend`,
  `TmuxInteractiveClaudeBackend`, `StubBackend`) on the same
  contract.
  **Alternatives considered**: a wrapping decorator at the flow
  layer (rejected — formula packs would each need to remember to
  apply it), or env mutation via os.environ before each subprocess
  call (rejected — breaks isolation across concurrent flow tasks
  in the same process).

- **Decision**: `_clean_env(extra_env)` always **strips** every
  `<PREFIX>_*` variant from the inherited orchestrator env first,
  then overlays the resolved `extra_env`. This runs even when
  `secret_provider is None` / `extra_env is None`.
  **Why**: Hardens the default. If the orchestrator process
  happens to have `SLACK_TOKEN_PLANNER` set (e.g. someone
  exported it for debugging), it must NEVER reach a builder
  subprocess. Stripping unconditionally means peer-role keys are
  scrubbed even if the caller forgets to wire a provider.
  **Why not**: Theoretically slower, but the strip is O(prefixes
  * env size), runs once per turn, and is dwarfed by subprocess
  spawn cost.

- **Decision**: `ChainSecretProvider` uses `dict.setdefault`
  semantics (first-hit-wins per resolved key).
  **Why**: Matches the documented precedence — `ChainSecretProvider([dotenv, env])`
  gives dotenv priority, which is the intuitive "rig overrides
  ambient shell" ordering. Same shape as Python's MRO / Linux's
  `PATH` semantics.

- **Decision**: Hand-roll a 30-line `_parse_dotenv` instead of
  taking a python-dotenv dep.
  **Why**: Core is meant to stay dep-light (it ships in pods).
  The grammar we support is documented and minimal: `KEY=value`,
  optional `export ` prefix, `#` comments, single/double quoted
  values. Anyone who wants `${VAR}` interpolation can write their
  own provider.

- **Decision**: Default scoped prefixes are `("SLACK_TOKEN",
  "GMAIL_CREDS", "ATTIO_TOKEN", "CALENDAR_CREDS")`.
  **Why**: These are the credential families po-formulas-software-dev
  consumes today. Adding `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`
  was rejected — those are already user-global and the
  orchestrator deliberately wants them inherited (the *Claude*
  subprocess needs `ANTHROPIC_API_KEY` to authenticate). Adding
  them to the strip set would break authentication.
  **How to extend**: pack authors pass a custom `prefixes=`
  tuple to `EnvSecretProvider(prefixes=...)` for non-default
  credential names; nothing in core needs to change.

- **Decision**: `StubBackend` gained a `captured_extra_env: dict[str,
  dict[str, str]]` field keyed by `session_id`.
  **Why**: The e2e isolation test (`tests/e2e/test_secret_injection.py`)
  needs to assert that role A and role B saw different `extra_env`
  values **without spawning a real Claude subprocess**. A pure-Python
  test hook on StubBackend lets us verify the role-isolation
  invariant directly. Tagged `Test hook only.` in the docstring so
  nothing in production wires it.

- **Decision**: Test layout — created
  `tests/test_agent_session_secrets.py` (unit) and
  `tests/e2e/test_secret_injection.py` (e2e), rather than
  extending an existing `tests/test_agent_session.py`.
  **Why**: There is no monolithic `tests/test_agent_session.py`
  in this rig — agent_session tests are split by concern
  (`_mail.py`, `_overlay.py`, `_tmux.py`, `_telemetry.py`). A
  dedicated `_secrets.py` matches the convention.

- **Decision**: Add `extra_env: object = None` kwarg to the two
  in-test fake backends (`RecordingBackend` in `_mail.py`,
  `_RecordingBackend` in `_overlay.py`) rather than making
  `AgentSession.prompt()` skip the kwarg when `extra_env is None`.
  **Why**: The `SessionBackend` Protocol now declares `extra_env`
  as part of the contract — every implementor must accept it.
  Conditionally suppressing the kwarg in the caller would let
  noncompliant backends quietly slip through; loud `TypeError`
  is the right signal.
