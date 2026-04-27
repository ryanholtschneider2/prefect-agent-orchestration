# Decision log — prefect-orchestration-o2r

- **Decision**: Per-rig overlay uses **per-field merge**, not full replacement.
  **Why**: Rigs commonly want to override one field (e.g. `name`) while
  inheriting the rest from the pack. Forcing rigs to copy every field
  would be brittle and surprising — matches the same "stack on top"
  philosophy as `overlay/**` already documented in pack-convention.md.
  **Alternatives considered**: Full replacement (rig file wins
  wholesale) — rejected as ergonomics regression.

- **Decision**: Identity-derived `{{agent_*}}` vars merge **behind**
  caller-supplied kwargs (caller wins).
  **Why**: Existing prompts that already pass e.g. `agent_name` via
  `**vars` keep working unchanged; identity is a fallback, not an
  override.
  **Alternatives considered**: Identity wins (would silently override
  callers, breaking current sw-dev pack callsites).

- **Decision**: Malformed TOML raises `IdentityLoadError`; missing
  files return `None` quietly.
  **Why**: Plan §risks: identity is identity — a malformed file should
  not silently render an anonymous prompt. Missing file is the
  documented "no identity" path and must stay backward-compatible.
  **Alternatives considered**: Swallow all errors (rejected — masks
  bugs); raise on missing (rejected — breaks legacy roles).

- **Decision**: `format_self_block` returns `""` when identity has
  no non-None fields (rather than emitting an empty `<self></self>`
  shell).
  **Why**: Keeps rendered prompts clean for partially-populated
  identity files; avoids a meaningless XML stub.
  **Alternatives considered**: Always emit the wrapper — rejected as
  visual noise.

- **Decision**: `mail_agent_name` falls back to `name` via a
  `effective_mail_agent_name` property rather than auto-filling at
  load time.
  **Why**: Keeps the loaded `Identity` faithful to the on-disk file
  (round-trippable, easy to test). Fallback is a render-time concern.
  **Alternatives considered**: Default `mail_agent_name = name` in
  loader — rejected as it muddles file fidelity.

- **Decision** (this iter, hermetic test fix): Added
  `monkeypatch.delenv("PO_RESUME", raising=False)` to the two
  `prompt_for_verdict` tests in `tests/test_parsing.py`.
  **Why**: `parsing.prompt_for_verdict` gained a `PO_RESUME=1`
  short-circuit (skip prompting if the verdict file already exists)
  after my iter-3 commit landed. The tests pre-write the verdict file
  to satisfy `read_verdict` — combined with `PO_RESUME=1` leaking into
  the test runner from the parent shell, that triggers the early
  return and the stub's `prompt()` is never called. Unsetting the env
  var per-test makes the tests hermetic regardless of caller
  environment, matching the "tests should not depend on shell state"
  convention.
  **Alternatives considered**: Move the verdict-file write below the
  `prompt_for_verdict` call — rejected; the stub doesn't actually write
  the file, the test does, so the file must be present before the call.
  Globally `monkeypatch.delenv` in a fixture — rejected; only these two
  tests are sensitive to `PO_RESUME`.

- **Decision** (iter 3, regression-gate fix): Added `prompt_for_verdict`
  helper to `prefect_orchestration.parsing` and a unit-test file
  `tests/test_parsing.py`.
  **Why**: Iter 1 + iter 2 regression-gate kept flagging the same
  collection-time `ImportError` — `po_formulas.software_dev` imports
  `prompt_for_verdict` from `prefect_orchestration.parsing`, but that
  name was never moved into core. Pytest aborts collection at first
  error, blocking the entire suite vs the 322-passing baseline. The
  helper is a 5-line wrapper (`sess.prompt(...)` + `read_verdict(...)`)
  that the pack already calls 5+ places — ships it in core where the
  pack expects it. Strictly speaking this is a separate bead, but the
  regression-gate refuses to clear without it and the fix is mechanical.
  **Alternatives considered**: Patch the pack to inline the helper —
  rejected, the pack lives in a sibling repo and the import boundary
  is what the pack contract advertises. Stub the import with a `try:
  except ImportError` shim — rejected, masks a real missing seam.

- **Decision** (iter 2, regression-gate fix): Extended `commands.core_verbs()`
  to also walk Typer sub-groups (`app.registered_groups`) so the nested
  `packs install/update/uninstall/list` verbs and the `packs` group name
  itself are surfaced — previously only top-level `@app.command()`
  callables were collected.
  **Why**: Regression-gate flagged `test_core_verbs_includes_all_typer_subcommands`
  as a new failure (4 verbs missing: `install`, `update`, `uninstall`,
  `packs`). The test correctly asserts that pack-shipped commands cannot
  shadow any core verb, including nested ones — a pack registering e.g.
  `install` in `po.commands` would otherwise be silently allowed and then
  collide once the orchestrator dispatched `po install …` to the packs
  sub-app. Touching `commands.py` (not `cli.py`) keeps the fix in the
  walker, not the surface.
  **Alternatives considered**: Loosen the test to only check top-level
  verbs — rejected; loses real shadow protection. Hard-code the four
  names — rejected; drifts the moment another sub-app is added.

- **Decision**: Pack-side wiring (passing `rig_path=…` from
  `po_formulas.software_dev.render`) deferred to a follow-up bead.
  **Why**: Plan §5 explicitly scopes this issue to the core seam.
  Pack lives in a separate repo (`software-dev/po-formulas/`) and
  ACs #1–#5 are all satisfiable by core + tests + docs alone (the
  smoke is a unit test, not a live pack invocation).
  **Alternatives considered**: Patch the sw-dev pack in-tree —
  rejected as cross-repo scope creep for this bead.
