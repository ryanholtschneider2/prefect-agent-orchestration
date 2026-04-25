# Decision Log — `prefect-orchestration-3cu.4`

## Build iter 1

- **Decision**: Pack lives at `/home/ryan-24/Desktop/Code/personal/nanocorps/po-attio/` (sibling to `prefect-orchestration/`), as its own directory tree. Not committed to the rig's git history.
  **Why**: Plan §"Pack location"; CLAUDE.md "land pack-contrib code in the pack's repo, not in the caller's rig-path" (issue `pw4`); mirrors `software-dev/po-formulas/` placement convention.
  **Alternatives considered**: nesting under `prefect-orchestration/packs/po-attio/` (would couple the pack's release cadence to core's), or under the rig as a one-off (violates `pw4`).

- **Decision**: Depend on PyPI `attio>=0.21` rather than `attio-python` or vendoring `httpx`.
  **Why**: Live PyPI verification — `attio` is published (version 0.21.2 at time of build), Speakeasy-generated, type-safe, covers all v2 endpoints; `attio-python` does not exist on PyPI. Plan §Risks called this out as something to verify at build time.
  **Alternatives considered**: direct `httpx` to `https://api.attio.com/v2/` (rejected — reinventing what an SDK already gives us); `attio-python` (does not exist).

- **Decision**: SDK client is **not cached**; `client()` constructs a fresh `attio.SDK` per command invocation, with lazy `from attio import SDK` inside the function so `po_attio.checks` and `po_attio.commands` import cleanly even when the SDK isn't installed.
  **Why**: Commands are one-shot CLI calls (short-lived process); fresh client surfaces transient auth/network issues honestly. Lazy import keeps `po doctor` informative when the dep hasn't been installed yet (env_set still works, workspace_reachable returns red with a clear hint).
  **Alternatives considered**: module-level singleton (would silently mask install issues); module-level `from attio import SDK` (would break import-time for `checks.py` users with no `attio` installed).

- **Decision**: SDK method names are called as Speakeasy generated them (`sdk.records.post_v2_objects_target_records_query`, `sdk.notes.post_v2_notes`, `sdk.objects.get_v2_objects`).
  **Why**: The `attio` SDK is auto-generated from the OpenAPI spec; method names track the HTTP verb + path. Wrapping them under prettier names creates a second mapping to maintain when the upstream surface drifts.
  **Alternatives considered**: hand-curated wrapper module (rejected — this is exactly the busywork the SDK exists to avoid).

- **Decision**: Defensive `_record_id` / `_attr_first` helpers cope with both `getattr` (Pydantic) and `dict` shapes for SDK responses, and the SDK exception path is `except Exception` printing the upstream message verbatim before `SystemExit(1)` (auth-specific path uses exit 2).
  **Why**: Plan §Risks called out "API surface drift … signature drift surfaces as upstream-SDK exception messages, which the commands print verbatim. No retries, no clever recovery." `BLE001` is intentional and noqa'd; clean failure-mode legibility is more important than typed error handling for a CLI wrapper this thin.
  **Alternatives considered**: typed exception classes per endpoint (rejected — too much speculative scaffolding for v1).

- **Decision**: `find` falls back to `people` when `object_type` is unrecognized (rather than raising).
  **Why**: Most Attio queries are people-search; an agent typo shouldn't fail a turn. Plan §`find` explicitly approved this fallback.
  **Alternatives considered**: strict validation with SystemExit on unknown values (rejected per plan).

- **Decision**: `attio-note` accepts `body=-` to read the note body from stdin.
  **Why**: Plan §`note` requires it; lets agents pipe long markdown content without escaping it as a single CLI flag.
  **Alternatives considered**: a separate `--body-file` flag (rejected — `-` is a more idiomatic Unix convention; no second mechanism to learn).

- **Decision**: `workspace_reachable` short-circuits to `yellow` when `ATTIO_API_KEY` is unset, rather than red or duplicating env_set's red.
  **Why**: Plan §`checks.py` mandates yellow. Avoids two red rows for the same root cause; keeps the doctor table legible.
  **Alternatives considered**: also-red (noisy duplicate); skip the row entirely (less informative).

- **Decision**: Tests are six minimal smoke checks under `po-attio/tests/test_smoke.py` — imports, env_set red/green, workspace_reachable yellow when unset, entry-points declared in pyproject, attio dep declared.
  **Why**: Plan §Test plan calls full coverage overkill. We verify the contract (entry points, status codes, importability) without hitting Attio's API. Live calls remain a manual smoke step.
  **Alternatives considered**: VCR-style recorded fixtures (rejected for v1 — adds a dep, recording requires a real key); mocking SDK call sites (rejected — we'd be testing our mock, not Attio).

- **Decision**: README, overlay/CLAUDE.md, overlay/.env.example all kept short and focused on the headline workflow (set key → `po doctor` → `po attio-*`). SKILL.md is the canonical longer-form reference.
  **Why**: `engdocs/pack-convention.md` § "Keep the skill short … repetition is fine — prompts are cheap" + "The pack owns policy and conventions; the vendor owns mechanics."
  **Alternatives considered**: a single fat CLAUDE.md duplicating SKILL.md (rejected — drift risk).

- **Decision**: File reservations registered only on the rig's planning-artifact paths (`decision-log.md`, `build-iter-1.diff`), not the sibling pack tree.
  **Why**: `mcp-agent-mail` reservations are project-relative (the project is the rig). Pack files at `/home/ryan-24/Desktop/Code/personal/nanocorps/po-attio/` live outside the rig project, so reservation collisions on them are not meaningful — no other PO worker is plausibly editing this brand-new directory.
  **Alternatives considered**: register `po-attio/**` on a separate `mcp-agent-mail` project (rejected — overkill for a one-time pack scaffold; no concurrent workers expected on a fresh sibling tree).
