# Decision log — prefect-orchestration-3cu.4

- **Decision**: Pack lives in sibling repo `/home/ryan-24/Desktop/Code/personal/nanocorps/po-attio/`, not in the rig.
  **Why**: `pw4` principle in rig CLAUDE.md — pack-contrib code lands in its own repo so packs ship and version independently of `prefect-orchestration` core.
  **Alternatives considered**: nesting under `packs/po-attio/` in the rig (rejected: violates pw4 and conflates pack lifecycle with core).

- **Decision**: Pinned `attio>=0.21` (Speakeasy-generated SDK) instead of `attio-python` from issue design.
  **Why**: Verified live on PyPI — `attio==0.21.2` exists; `attio-python` does NOT. Issue design text was incorrect.
  **Alternatives considered**: forking the SDK (overkill); HTTP via `httpx` directly (loses typed shapes, more code to maintain).

- **Decision**: Lazy `from attio import SDK` import inside `client()` rather than module-level.
  **Why**: keeps `po_attio.checks` importable for `po doctor` even if the SDK isn't installed yet; lets `env_set()` still report state usefully.
  **Alternatives considered**: top-level import (cleaner but cascades red across both checks on missing dep); try/except at module top (ugly).

- **Decision**: `workspace_reachable()` short-circuits to **yellow** when `ATTIO_API_KEY` is unset (instead of red).
  **Why**: `env_set()` already covers that case as red; double-counting it as red on the second check is noisy. Yellow signals "couldn't run, see prior row."
  **Alternatives considered**: red (noisy); skip entirely (loses signal that the check exists).

- **Decision**: 8-char truncated key preview in `env_set()` green message; never the full key.
  **Why**: `po doctor` output is meant to be pasted/screenshotted into status updates; full keys are credentials.
  **Alternatives considered**: print full key (security risk); print only "set" (no signal that the right key is loaded).

- **Decision**: `find` defaults to `people` and silently falls back to people on unknown `--object-type`.
  **Why**: most agent calls in nanocorp flows search people; companies is the only other common object; falling back keeps the command useful when the agent hallucinates an object name.
  **Alternatives considered**: error on unknown type (worse UX for one-shot agent calls); enumerate all object types from `sdk.objects.get_v2_objects()` first (extra round-trip on every call).

- **Decision**: `note --body=-` reads stdin; `--title` defaults to first 80 chars of body.
  **Why**: matches the `--body=-` convention from `po-mail` send and keeps long markdown notes pipeable; auto-title removes a required arg.
  **Alternatives considered**: require `--title` always (worse UX); fixed title like "Note from Claude" (uninformative).

- **Decision**: Defensive `_record_id` and `_attr_first` helpers in commands.py.
  **Why**: Speakeasy SDKs return Pydantic models in some paths and raw dicts in others depending on response shape; tolerating both prevents `AttributeError` on minor SDK bumps.
  **Alternatives considered**: tight Pydantic-only access (breaks on dict-shaped responses); `.dict()` everywhere (loses type info).

- **Decision**: Tests offline-only — no live API calls, no SDK network mocks.
  **Why**: Live tests need a credential nobody has in CI; mocking the SDK couples tests to internal SDK shapes that drift across versions. Smoke tests cover env-handling paths and pyproject contract; the doctor's `workspace_reachable` covers the live-API health signal at runtime.
  **Alternatives considered**: `respx`/`responses` against the SDK's underlying httpx client (brittle to SDK version bumps); recorded fixtures (stale fast, hard to refresh without a key).

- **Decision**: Pack initialized as standalone git repo with its own commit history; only `decision-log.md` + `plan.md` + `build-iter-1.diff` land in the rig.
  **Why**: matches pw4 layout; lets the pack get its own remote later without rewriting history.
  **Alternatives considered**: monorepo subtree (premature); committing pack files into the rig (violates pw4).

- **Decision**: `pip index`/PyPI pre-flight verification of `attio` happened before pinning.
  **Why**: avoid shipping a broken `pyproject.toml` that fails `po install` for every consumer.
  **Alternatives considered**: trusting the issue's design text (already known to be wrong about the package name).

## Iter 1 — re-run after `po retry`

- **Decision**: Sibling pack tree was preserved across the rig's `po retry` archival (which only touched `.planning/.../*.bak-*`); did not re-create files.
  **Why**: `po retry` archives the rig's run-dir, not external sibling repos. Re-creating identical files would churn the pack repo's history needlessly.
  **Alternatives considered**: rebuilding from scratch (wasteful); `git reset --hard` in the pack (destructive, loses lint commit `9ab15b9`).
  **Verification**: re-ran the 6 smoke tests → all pass (0.04s) against the existing tree.
