# Verification Report: prefect-orchestration-4xo

## Acceptance Criteria Verification

| # | Criterion | Method | Result | Evidence |
|---|-----------|--------|--------|----------|
| 1 | `render_template(agents_dir, role)` checks `<agents_dir>/<role>/memory/MEMORY.md`; if present, prepends content as `<memory>...</memory>` block | Unit test | PASS | `test_memory_block_prepended_when_present` — asserts output starts with `<memory>\nremember: foo\n</memory>\n\n` |
| 2 | Backwards compat: roles without `memory/` render unchanged | Unit test | PASS | `test_no_memory_dir_renders_unchanged` + all 12 pre-existing template tests pass byte-identically |
| 3 | `pack-convention.md` documents the new layout | Doc edit | PASS | New "Per-role memory (4xo)" section added at line ~254; directory layout block also updated to list `memory/MEMORY.md` |
| 4 | Smoke: a role's prompt sees its own MEMORY.md content on second turn after writing it on first | Unit test | PASS | `test_smoke_second_turn_sees_first_turn_memory` — turn 1 has no `<memory>`, write file, turn 2 contains the just-written content inside `<memory>...</memory>` |

## Bonus checks

- `test_empty_memory_file_renders_no_block` — whitespace-only files emit nothing
- `test_memory_block_precedes_self_block` — ordering is `<memory>` → `<self>` → body
- `test_rig_overlay_memory_overrides_pack_memory` — rig overlay wins
- `test_memory_content_is_not_substituted` — verbatim, literal `{{...}}` doesn't raise

## Regression Check

- Baseline tests: 27 failed, 445 passed, 2 skipped (recorded 2026-04-27 by PO triage)
- Final tests:    10 failed, 581 passed, 8 skipped
- New tests added: 7 (all in `tests/test_templates.py`)
- Regressions: NONE — every current failure is a subset of the baseline failures (test_cli_packs.*, test_deployments::test_po_list_still_works, test_mail::test_prompt_fragment_exists_and_mentions_inbox). Pre-existing.

## Live Environment Verification

- Environment: NONE — pure templating function with no runtime services. AC #4's "smoke" is a two-turn unit test simulating "agent writes memory on turn 1, sees it on turn 2" without spinning up Claude. The plan explicitly identified this as the appropriate verification ("Simulates 'agent writes memory on turn 1, sees it on turn 2' without spinning up a real Claude session").
- e2e layer skipped per the rig's `.po-env` (`PO_SKIP_E2E=1`); change is in the templating layer with no subprocess interactions.

## Decision Log

- **Decision**: Memory loaded AFTER `{{var}}` substitution and prepended to the already-substituted result.
  **Why**: Memory is verbatim agent-authored content. If we substituted inside it, agent text containing `{{...}}` would crash with KeyError.
- **Decision**: Block ordering `<memory>` → `<self>` → body (not the reverse).
  **Why**: Plan §5 — memory is the most static / oldest context, identity is per-role config, body is the work prompt. Outermost-first matches Claude Code's system-memory-above-project layering.
- **Decision**: Rig overlay wins (file-level), not per-line merge.
  **Why**: MEMORY.md is unstructured prose, not config. Per-line merge has no sane semantics. Documented in pack-convention.md.
- **Decision**: No size cap in v1.
  **Why**: Plan §7 — matches Claude Code; agent owns the file and is expected to curate it. Mail's cap exists because mail grows unboundedly.
- **Decision**: Implemented inline (no subagents).
  **Why**: 30-line change to one templating function + 7 unit tests + one doc section. Per user's `~/.claude/CLAUDE.md` rule "When to use PO vs implement inline" — small, low-risk, single-file changes go inline.

## Confidence Level

**HIGH** — All 4 acceptance criteria verified by deterministic unit tests against the actual templating function. No regressions vs baseline. AC #4's "smoke" is appropriately scoped to a two-turn unit test (the plan agreed this was correct verification for a pure-templating change).
