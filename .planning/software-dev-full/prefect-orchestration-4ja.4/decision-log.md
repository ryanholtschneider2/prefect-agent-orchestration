# Decision log — prefect-orchestration-4ja.4

- **Decision**: New standalone module `pack_overlay.py` rather than extending the in-progress `prefect_orchestration/packs.py` (untracked, owned by a sibling issue).
  **Why**: That file is being built by another worker. Touching it would collide and conflate concerns — `packs.py` does install/uninstall lifecycle; `pack_overlay.py` does session-time content materialization. They share `PO_ENTRY_POINT_GROUPS` constants by coincidence, not coupling. Reconciliation (sharing a single discovery helper) can land in a follow-up once both are merged.
  **Alternatives considered**: extend `packs.py` (rejected — concurrent edit risk, scope creep); inline the discovery in `agent_session.py` (rejected — would force unit tests to spin up Claude).

- **Decision**: Lazy materialization in `AgentSession.prompt()` guarded by a `_materialized` flag, not in `__post_init__`.
  **Why**: Plan §AgentSession integration. Construction stays cheap; tests that build `AgentSession` with stub backends don't need to mock `materialize_packs` unless they want to assert against it. Failure during materialization is logged-and-swallowed, not raised.
  **Alternatives considered**: eager in `__post_init__` (worse for tests, fails fast on disk errors); per-turn (wasteful).

- **Decision**: Per-role overlay processed *before* pack-wide overlay. Both use skip-existing semantics.
  **Why**: Triage flagged precedence as an open question. Plan §Per-role overlay precedence picks "role overrides pack-wide on conflict". Easiest expression: lay role files down first; pack-wide then sees them as "existing" and skips. Single rule, no special-cased override logic.
  **Alternatives considered**: explicit override flag on per-role files (more code, more docs).

- **Decision**: "Existing" is filesystem presence, not git-tracked status.
  **Why**: Triage open question. Plan picks filesystem presence — matches the literal AC ("existing files in cwd") and avoids a `git ls-files` shell-out per file. Simpler, faster, and user-authored untracked files (work-in-progress) also win, which is the intuitive behavior.
  **Alternatives considered**: git-tracked check (heavier, surprising for untracked files).

- **Decision**: Skills always overwrite via `shutil.copy2`; we only touch `<rig>/.claude/skills/<pack-name>/` for installed packs.
  **Why**: AC 4 mandates overwrite. Scoping to `<pack-name>/` subdir leaves user-authored or plugin-loaded skills under sibling subdirs alone — explicit test for this.
  **Alternatives considered**: nuke-and-rewrite per pack subdir (same effect, more disk churn); track per-file checksums to skip no-op writes (premature optimization).

- **Decision**: Discovery probes `<dist-root>/overlay/` first, then `<package-root>/overlay/`.
  **Why**: Risk surfaced in plan: editable installs ship overlay at the dist root (sibling to `pyproject.toml`), wheel installs typically embed it inside the importable package. Supporting both lets pack authors choose the layout that matches their build tooling without surprising users.
  **Alternatives considered**: dist-root only (breaks wheel installs); module-root only (forces packs to restructure); a `[tool.po]` config field (more ceremony, no real win — the probe is two stat calls).

- **Decision**: Materialization failure is logged via `logger.exception` and swallowed.
  **Why**: Pack overlay/skills are best-effort surfacing of pack content. A disk error or permission glitch must not block a real agent turn — user can still operate without the overlay. Plan risk section ("API contract: ... safe defaults; non-breaking").
  **Alternatives considered**: re-raise (turns a soft surface into a hard dependency); silent swallow without log (hides bugs).
