# Plan — prefect-orchestration-915.child1

Add a one-line docstring summary at the top of
`prefect_orchestration.beads_meta._bd_dep_list` describing what the
function returns. Single-line, docs-only edit.

## Affected files

- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/beads_meta.py`
  — `_bd_dep_list(...)` (currently lines ~324–339). Edit the docstring
  only; signature, body, callers untouched.

## Approach

Today's docstring leads with the implementation detail
(`"Run \`bd dep list ... --json\`."`) rather than the contract.
Callers reading the symbol in an editor see the shell command before
they see what they get back. Reorder so the summary line states the
return value first; demote the shell-out sentence to the body. Existing
notes about `[]`-on-failure and `rig_path` cwd semantics stay verbatim
on the lines below.

Concretely the new summary line is something like:

> Return the dep-graph rows for *issue_id* as a list of dicts (empty on failure).

The shell-out detail moves to the second line:

> Shells out to `bd dep list <id> --direction=<dir> [--type=<t>] --json`.

No call sites change — `_bd_dep_list` is a private helper used by the
graph-discovery path in `beads_meta.py`. No public API surface is
touched. No tests reference the docstring text.

## Acceptance criteria (verbatim from issue)

- function has clear summary line
- tests still pass

## Verification strategy

- **Clear summary line** — `head -n 5` of the function shows the
  one-line summary leading with "Return …" before any shell-out
  detail. (Manual read; trivially eyeballable.)
- **Tests still pass** — run the existing module test suite:
  `uv run python -m pytest tests/test_beads_meta.py` should still pass
  every test it passed pre-change. Full unit suite
  (`uv run python -m pytest`) should also stay green; baseline shows
  762 passed / 1 skipped (see `baseline.txt`).

## Test plan

- **unit** — re-run `tests/test_beads_meta.py` (and the full unit
  suite) post-edit. No new tests required: docstrings have no runtime
  behavior to assert, and the existing tests already cover the
  function's return-value contract on success and failure paths.
- **e2e / playwright** — N/A. Docs-only edit to a private helper.

## Risks

- None of substance. Docstring edit on a private helper:
  - No migrations.
  - No API contract changes (the docstring is not part of any public
    schema; callers don't import it).
  - No risk of breaking consumers — the function signature, body, and
    return shape are unchanged.
- Only failure mode is a typo introduced while reformatting the
  docstring; pytest collection would still pass since docstrings are
  not parsed at collection time. Caught by a quick visual diff before
  commit.
