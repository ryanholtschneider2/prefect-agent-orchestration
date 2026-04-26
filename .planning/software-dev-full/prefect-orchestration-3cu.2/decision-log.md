# Decision log — prefect-orchestration-3cu.2 (build iter 1)

- **Decision**: SDK-only implementation; no shell-out to `gcloud` or `gcal`.
  **Why**: `gcloud` has no native calendar verbs (just auth helpers), and `gcal`/`khal` are not ubiquitous. Headless PO agents cannot run interactive OAuth, so SDK + service-account JSON / ADC is the only path that works in a tmux/Prefect-worker context. Plan §"Auth" + §Risks.
  **Alternatives considered**: hybrid (`gcloud auth` for tokens + SDK for API) — rejected because resolving creds from `gcloud auth` already happens automatically via `google.auth.default()` (ADC well-known file fallback).

- **Decision**: `gcal-create` reads a JSON Event resource from stdin.
  **Why**: Matches the v3 API contract directly; handles nested objects (attendees, conferenceData) cleanly; one obvious format. Plan §Approach.
  **Alternatives considered**: ICS/RFC5545 (heavyweight, needs a parser); key=value flags (can't express nested attendees).

- **Decision**: `gcal-free` requires explicit `--user --start --end` (no defaults).
  **Why**: Predictability over convenience for a command an LLM will call. A "default to today" implicit behavior would surprise the agent. Plan §Approach.
  **Alternatives considered**: default `--end` to "+1h" or end-of-day — rejected.

- **Decision**: Single auth seam at `_client.build_service` + `_client.resolve_creds`; tests mock at this seam, NOT inside `googleapiclient`.
  **Why**: Robust to SDK upgrades; isolates our intent from Google's discovery internals; lets `FakeService` expose only the surface we use (`events()`, `freebusy()`, `calendarList()`). Plan §"Tests".
  **Alternatives considered**: patch `googleapiclient.discovery.build` (couples tests to SDK internals); record/replay HTTP fixtures (overkill).

- **Decision**: `calendar_reachable()` short-circuits **yellow** (not red) when creds are missing/invalid.
  **Why**: Avoid double-counting — `creds_present()` already reports red in that case. Reachability can't be evaluated without creds, so yellow is the honest signal. Plan §Doctor checks.
  **Alternatives considered**: red (double-counts); green (lies); skip (loses the row).

- **Decision**: Pack lives at `/home/ryan-24/Desktop/Code/personal/nanocorps/po-gcal/`, sibling to `prefect-orchestration/`.
  **Why**: Per `pw4` convention and the triage flag, packs are NOT subfolders of core. Only this rig's decision-log + plan land in `prefect-orchestration/`. Plan §Affected files + §Risks.
  **Alternatives considered**: `prefect-orchestration/packs/po-gcal/` (rejected — violates pack/core split).

- **Decision**: Skipped `mcp-agent-mail` file reservations for this build.
  **Why**: Pack lives in a sibling repo outside the registered project workspace; the reservation server scopes to the project root. No concurrent worker can collide with paths under `../po-gcal/` because no PO worker outside this issue knows about it. Reservations would fail anyway since the path falls outside `ensure_project`'s tracked tree.
  **Alternatives considered**: `ensure_project` for `../po-gcal/` separately — overkill for a pack with no other concurrent claimants.

- **Decision**: Overlay `CLAUDE.md` wrapped in `<!-- po-gcal:begin -->` / `<!-- po-gcal:end -->` markers.
  **Why**: `pack_overlay.apply` (per `4ja.4`) merges overlay snippets idempotently — markers let re-application replace the block in place rather than duplicating.
  **Alternatives considered**: full-file overlay (clobbers caller's `CLAUDE.md`); appendage with no markers (duplicates on re-apply).
