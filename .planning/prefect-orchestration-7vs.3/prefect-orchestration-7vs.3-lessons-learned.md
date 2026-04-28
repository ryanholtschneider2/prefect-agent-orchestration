# Lessons Learned: prefect-orchestration-7vs.3

## Difficulties

- **bd JSON closure-reason field naming is non-uniform** — builder
  defended against both `closure_reason` and `reason` keys in the
  parsed `bd show <id> --json` output. Worth standardising upstream
  in `beads_meta._bd_show` someday so consumers don't each carry
  fallback chains.
- **Pack pre-existing test collection errors** (`test_software_dev_pack_path*.py`
  reference removed `_CODE_ROLES` / `_resolve_pack_path`) shadow part
  of the pack test suite. Out of scope for this issue but flagging —
  these should be cleaned up or pinned to the right commit.

## What worked

- **Synchronous `sess.prompt()` + `bd show` after** rather than
  `watch()` + async dispatch was the right call for the pilot. The
  agent calls `bd close` *during* its turn, so by the time `prompt()`
  returns the bead is already in its final state. `watch()` becomes
  load-bearing for fully-async roles in later children, not this one.
- **Idempotent `create_child_bead`** (swallow "already exists" errors)
  — Prefect task retries and ralph re-entries don't fail spuriously
  on the bead-create step.

## Patterns to consider for global notes

- "Smallest blast radius pilot" — when migrating a multi-role
  orchestration contract, do one role end-to-end before fanning out.
  Other roles staying on the legacy contract is a feature, not a
  shortcut.
