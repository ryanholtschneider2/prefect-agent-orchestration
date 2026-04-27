# Plan: prefect-orchestration-ddh — per-agent secret injection

## Affected files

- **New** `prefect_orchestration/secrets.py` — `SecretProvider` Protocol, `EnvSecretProvider`, `DotenvSecretProvider`, `role_env_key()` slug helper, `resolve_role_env()` orchestrator entrypoint.
- `prefect_orchestration/agent_session.py` — extend `SessionBackend.run(...)` with `extra_env: Mapping[str, str] | None = None`; thread through `ClaudeCliBackend`, `TmuxClaudeBackend`, `TmuxInteractiveClaudeBackend`, `StubBackend`. Add `secret_provider: SecretProvider | None = None` field to `AgentSession`; resolve role-scoped env before each `backend.run` call. Tighten `_clean_env()` to strip *all* `*_<ROLE_KEY>` scoped vars before re-keying so peer-role secrets don't leak.
- `prefect_orchestration/__init__.py` — re-export `SecretProvider`, `EnvSecretProvider`, `DotenvSecretProvider`.
- `engdocs/pack-convention.md` — document `<TOKEN>_<ROLE_UPPER_UNDERSCORE>` naming, per-rig `.env` overlay, precedence (CLI flag > rig `.env` > process env), normalization rule (hyphen / dot → underscore, uppercased; matches tmux session-name sanitization).
- `tests/test_secrets.py` (new) — unit coverage for provider impls + slug + role-scoped re-key + leakage strip.
- `tests/test_agent_session.py` — extend with a fake backend that captures `extra_env`, verify role A sees `SLACK_TOKEN`, role B in same flow does not.
- `tests/e2e/test_secret_injection.py` (new, lightweight) — `po run` against a stub formula that prints `os.environ` to a verdict file; assert role isolation.

## Approach

The seam is the `SessionBackend.run` boundary — every role's session goes through it, and the existing `_clean_env()` already shapes child env for the Claude CLI subprocess. We extend that boundary instead of inventing a parallel one.

1. **Protocol & impls (`secrets.py`).**
   - `SecretProvider` defines `get_role_env(role: str) -> dict[str, str]`. Returns the *re-keyed* dict ready to merge into child env (e.g. `{"SLACK_TOKEN": "xoxb-…"}`), not the raw `SLACK_TOKEN_ACQUISITIONS_BOT` form.
   - `EnvSecretProvider(prefixes: tuple[str, ...] = ("SLACK_TOKEN", "GMAIL_CREDS", "ATTIO_TOKEN", "CALENDAR_CREDS"))` scans `os.environ` for `<PREFIX>_<ROLE_KEY>` and re-keys to `<PREFIX>`. Prefixes configurable so callers can extend without subclassing.
   - `DotenvSecretProvider(path: Path, prefixes=...)` parses a `.env` file (no python-dotenv dep — implement a 30-line parser to avoid pulling in a transitive). Used for per-rig overlays.
   - `ChainSecretProvider(providers: list[SecretProvider])` (small, useful for precedence: dotenv overlay then env fallback).
   - `role_env_key(role: str) -> str` normalizes hyphens, dots, spaces → `_`, uppercases. `plan-critic` → `PLAN_CRITIC`. Centralized so every read site agrees.

2. **AgentSession wiring.**
   - New optional field `secret_provider: SecretProvider | None = None`. When `None`, behavior is identical to today (no env injection, full backwards compat).
   - In `prompt()`, before calling `backend.run`, compute `extra_env = self.secret_provider.get_role_env(self.role) if self.secret_provider else {}` and pass via the new kwarg.

3. **Backend changes.** All four backends gain `extra_env: Mapping[str, str] | None = None` on `run(...)`. The merge happens inside each backend right where it builds child env:
   - For `ClaudeCliBackend`: `subprocess.run(..., env={**_clean_env(strip_role_scoped=True), **(extra_env or {})})`. (Currently it doesn't pass `env=` at all — child inherits everything; we make this explicit.)
   - For both tmux backends: same merge, fed into the existing `env=_clean_env()` site.
   - For `StubBackend`: stash `extra_env` on a class attr (or write to a verdict file) so tests can assert what would have been injected without launching anything.

4. **Leakage scrub (`_clean_env(strip_role_scoped=True)`).** Strip every key matching `^[A-Z][A-Z0-9_]*_[A-Z0-9_]+$` whose suffix is a known role-key (or, simpler: strip anything matching the configured prefixes followed by `_`). Then overlay the role's re-keyed subset. End state: child env has `SLACK_TOKEN` (re-keyed for *this* role) and zero `SLACK_TOKEN_*` orchestrator vars. Other roles' secrets are not present at all.

5. **Precedence.** `ChainSecretProvider([DotenvSecretProvider(rig/.env), EnvSecretProvider()])` — first hit wins. Documented in pack-convention.md. Per-rig `.env` is *not* loaded automatically by core (no surprise file reads); callers (registry factory, future `build_registry` from u1x) opt in.

6. **Logging hygiene.** No secret values in logs. Existing `_clean_env` doesn't log; backend exception messages show `proc.stderr[:2000]` and `stdout[-2000:]` — those are agent-facing, not env-facing, so no change needed. Add a TODO note in `secrets.py` to keep `__repr__` of providers from echoing values (use `{len(d)} role-secrets`). Verdict / run-dir artifacts never see env, so no scrub there.

7. **RoleRegistry.** This bead does **not** depend on `u1x` (RoleRegistry-to-core) being merged. We wire at `AgentSession` construction; whichever caller (`software_dev.RoleRegistry` today, `prefect_orchestration.role_registry` after u1x) sets `secret_provider=` gets injection. The pack will be updated in a follow-up — out of scope here. Plan calls this out so the critic doesn't flag it as a missing wire.

## Acceptance criteria (verbatim)

1. `SecretProvider` Protocol in `prefect_orchestration` with `EnvSecretProvider` + `DotenvSecretProvider` implementations.
2. `RoleRegistry` / backend launches the role's tmux session with role-scoped env vars (`SLACK_TOKEN`, `GMAIL_CREDS`, etc., re-keyed from the per-role originals).
3. `pack-convention.md` documents the `SLACK_TOKEN_<ROLE>` naming + per-rig overlay.
4. Smoke: a role with `SLACK_TOKEN` configured can post to Slack; another role in the same flow without it cannot see it.

## Verification strategy

- **AC1** — `tests/test_secrets.py`: `isinstance(EnvSecretProvider(), SecretProvider)` (runtime-checkable Protocol); both impls return expected dicts under controlled fixtures (monkeypatched env, tmp dotenv).
- **AC2** — `tests/test_agent_session.py`: fake backend that captures `extra_env` from `run()`. Set env `SLACK_TOKEN_PLANNER=xoxb-A`, `SLACK_TOKEN_BUILDER=xoxb-B`. Construct two `AgentSession`s sharing a single `EnvSecretProvider`. Assert `planner` session received `{"SLACK_TOKEN": "xoxb-A"}` and `builder` got `{"SLACK_TOKEN": "xoxb-B"}`. Negative: also assert `SLACK_TOKEN_BUILDER` is *not* in planner's resolved env.
- **AC3** — Documentation diff in `engdocs/pack-convention.md`: section "Per-agent secrets" with naming rule + slug normalization table + precedence + example `.env` snippet. Critic / reviewer reads file directly.
- **AC4** — `tests/e2e/test_secret_injection.py`: a tiny stub formula (or reuse the dry-run path) that runs two `AgentSession`s with `secret_provider=ChainSecretProvider(...)`. Backend is `StubBackend`-like that writes `os.environ` snapshot to `<run_dir>/<role>-env.json`. Test reads both files: role A's snapshot contains `SLACK_TOKEN`, role B's does not. (Cannot do a real Slack `chat.postMessage` in CI without a real token; the role-isolation invariant is what AC4 actually requires us to prove. If a real Slack token is present in the dev's env, an opt-in `@pytest.mark.live_slack` test posts to `#ryan_claude_code` and asserts a 200 — gated off in CI.)

## Test plan

- **Unit** — `tests/test_secrets.py` (provider behavior + slug). `tests/test_agent_session.py` extension (per-role env routing through backends). Run `uv run python -m pytest tests/test_secrets.py tests/test_agent_session.py`.
- **E2E** — `tests/e2e/test_secret_injection.py` exercising stub-backend + AgentSession path (no Prefect server needed; constructs backends directly). No `po run` subprocess required — we're testing the seam, not the full pipeline.
- **Playwright** — N/A (no UI).
- Existing test suite must stay green: `extra_env` defaults to `None`, all current callers untouched.

## Risks

- **API contract change on `SessionBackend.run`.** Adding a kwarg with default `None` is non-breaking for keyword callers but breaks anyone using positional args. Grep confirms all in-repo callers use keywords (`session_id=`, `cwd=`, `fork=`, `model=`); pack code does too. Low risk; document in CHANGELOG-style note in commit.
- **Subprocess env explicitness.** `ClaudeCliBackend` currently passes no `env=`, so child inherits all of orchestrator's env. Switching to explicit `env=` (so we can scrub scoped keys) means a stripped-down env going to the child — could break agents that rely on some random orchestrator env var. Mitigation: start from `_clean_env()` (full copy minus `ANTHROPIC_API_KEY`), strip *only* the scoped-prefix keys, then overlay role-scoped. Net behavior matches today's for any role without secrets configured.
- **Dotenv parser.** Hand-rolled; handles `KEY=val`, `KEY="val with spaces"`, `KEY='val'`, `# comments`, blank lines. Does NOT handle escaped quotes, multi-line values, or `export` prefixes. Documented as such; if requirements grow, swap for `python-dotenv` later.
- **Role-name normalization mismatch.** If `role_env_key("plan-critic")` produces `PLAN_CRITIC` but a user's `.env` has `SLACK_TOKEN_PLAN-CRITIC`, lookup misses. Documented + symmetric: same normalizer used both for setup docs and at lookup time.
- **No migration / no schema change.** Pure additive feature; no DB / Prefect-deployment / bd-schema impact.
- **Out of scope (called out):** Vault, OAuth refresh, secret rotation, real Slack live test. Protocol shape is forward-compatible (a future `VaultSecretProvider` just implements `get_role_env`).
