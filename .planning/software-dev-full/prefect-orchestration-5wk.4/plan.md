# Plan: prefect-orchestration-5wk.4 — snakes-demo rig provisioning script

## Revision history

- **iter-1 → iter-2**: critic flagged two issues + three nits. Revised:
  1. Pin `languages.txt` content to the canonical slot-N→language mapping
     copied verbatim from `bd show prefect-orchestration-5wk` (no
     re-derivation).
  2. Move `test_snakes_demo_provision.py` from `tests/` (unit) to
     `tests/e2e/` (real subprocesses violate unit-layer rules).
  3. Add inline comment explaining `git add -A` is acceptable here.
  4. Add header comment to `languages.txt` declaring the consumer
     contract.
  5. Heredoc'd CLAUDE.md mentions the `bd init --server` upgrade path.

## Affected files

- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/scripts/snakes-demo/provision-rig.sh` (new, executable, +x)
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/scripts/snakes-demo/languages.txt` (new — canonical 100-language list, source of truth shared with `5wk.5` seeder)
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/e2e/test_snakes_demo_provision.py` (new — e2e smoke test that subprocesses real `bash`/`git`/`bd` into a tmpdir)

No changes to PO core, formulas, or runtime.

## Approach

Single bash script under `scripts/snakes-demo/`. Uses `set -euo pipefail`,
shellcheck-strict-style quoting, `[[ ]]` tests, manual flag parsing for
`--force` and `--remote <url>`. Behavior:

1. **Resolve target path**: `RIG_PATH="${RIG_PATH:-$HOME/Desktop/Code/personal/snakes-demo}"`.
2. **Idempotency gate**:
   - If `$RIG_PATH` exists and contains `.beads/` (our marker) → with `--force`, sanity-refuse the wipe if path is `$HOME`, `/`, or empty, then `rm -rf -- "$RIG_PATH"`. Without `--force`, exit 1 with a clear "rig exists; use --force to wipe" message.
   - If `$RIG_PATH` exists but lacks `.beads/` → refuse unconditionally (don't wipe unrelated user data even with `--force`).
3. **Provision**:
   - `mkdir -p "$RIG_PATH"` then `cd "$RIG_PATH"`.
   - `git init -b main` (initial-branch flag); fall back to `git init && git symbolic-ref HEAD refs/heads/main` for older git.
   - Resolve git author from env (`GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL`) → fallback to global `git config user.name` / `user.email`; if neither is set, exit 1 with explicit message (no silent empty-author commit).
   - Heredoc-write `README.md` (what the demo is, how to run), `CLAUDE.md` (per-child agent guidance verbatim from issue: "You are implementing the game Snake. Each child bead asks for a single language…" plus a one-liner pointing to `engdocs/languages.txt` plus a "Operators running parallel children should switch to `bd init --server` and start `dolt sql-server` out-of-band — see prefect-orchestration repo CLAUDE.md" upgrade-path note).
   - `cp` `scripts/snakes-demo/languages.txt` (located via `$BASH_SOURCE` so cwd-independent) to `$RIG_PATH/engdocs/languages.txt`. Single source of truth — script does not embed the list.
   - `bd init` (embedded mode — single-tenant demo rig; see `bd init --server` note in heredoc'd CLAUDE.md for parallel-run operators).
   - Optional `--remote <url>` → `git remote add origin <url>` (no push).
   - `git add -A && git commit -m "Initial snakes-demo rig"` — comment in script: `# git add -A is safe here: greenfield rig, no concurrent workers possible by definition (this script just created the dir).` Otherwise rig CLAUDE.md warns against `-A`.
4. **Locate self via `$BASH_SOURCE`** so the languages.txt sibling resolves regardless of cwd.

### `languages.txt` content

100 lines, format `<N>\t<language>`, N=1..100. Content **pinned to the
parent epic `prefect-orchestration-5wk` description** (slots 1..100 in
the order listed there: `1\tPython`, `2\tRust`, `3\tGo`, …, `100\tLogo`).
First line is a comment:

```
# canonical slot ordering — do not reorder; consumed by provision-rig.sh and seed-children.sh (5wk.5)
```

(Comment line is skipped by both consumers; the 100 data lines start
after it.)

The CLAUDE.md content matches the issue description verbatim ("You are
implementing the game Snake…") and adds the languages.txt pointer + the
`bd init --server` upgrade note.

## Acceptance criteria (verbatim from issue)

- Script idempotent.
- Resulting rig has `.git`, `.beads`, `README.md`, `CLAUDE.md`, `engdocs/languages.txt`, no `snakes/` yet.
- Lint passes (shellcheck strict mode).

## Verification strategy

- **Idempotent**: `tests/e2e/test_snakes_demo_provision.py` runs the script twice into `tmp_path` — second invocation without `--force` exits non-zero with "rig exists" message; with `--force` succeeds and tree matches. Manual: `RIG_PATH=/tmp/snakes-demo-test bash scripts/snakes-demo/provision-rig.sh` twice.
- **Resulting tree**: pytest asserts existence of `.git/`, `.beads/`, `README.md`, `CLAUDE.md`, `engdocs/languages.txt`, and absence of `snakes/`. Asserts `engdocs/languages.txt` has exactly 100 data lines (one comment line + 100), and that line N (for sample N=1, 50, 100) matches the canonical mapping (`Python`, `F#`, `Logo`).
- **Shellcheck strict**: manual `shellcheck -S style scripts/snakes-demo/provision-rig.sh` exits 0; run as part of build step before declaring done. (Also enforced by repo `bd doctor --check=conventions` lint pass over `scripts/`.)

## Test plan

- **e2e** (`tests/e2e/test_snakes_demo_provision.py`): subprocess the script into `tmp_path`, assert tree, assert idempotency (second run fails without `--force`, succeeds with), assert `--remote` adds an origin, assert canonical-language-list integrity. Skip with `pytest.skip` if `bd` or `git` not on PATH. Real `bash`/`git`/`bd` subprocess → belongs in e2e per repo CLAUDE.md unit-layer rules ("no real subprocesses"). Sits alongside existing `test_po_*_cli.py` subprocess tests.
- **unit**: N/A — would violate unit-layer subprocess prohibition.
- **playwright**: N/A — no UI.

This rig's `.po-env` sets `PO_SKIP_E2E=1`, so the actor-critic loop skips
e2e. That's fine: the shellcheck lint AC is enforced in the build step
(not via this test), and the test exists for manual `uv run python -m
pytest tests/e2e/` runs before release. Loop coverage is not load-bearing
on this test.

## Risks

- **Destructive `rm -rf`**: gated behind `.beads/` marker check + refuse-if-`$RIG_PATH`-equals-`$HOME`-or-`/`-or-empty guard. Without `--force` the script never deletes. Tests cover both paths.
- **`bd init` mode**: embedded-dolt is the simpler choice for a single-tenant demo rig. Repo CLAUDE.md prefers dolt-server for parallel epics; concurrent `bd update` writes from parallel `po run`s in the *same* rig will hit dolt-embedded's exclusive lock. **Decision**: still go embedded for `5wk.4` (the script doesn't dictate run-time concurrency). Heredoc'd CLAUDE.md documents the `bd init --server` upgrade path so operators reading just the rig's docs find it.
- **Languages list immutability**: shipped `languages.txt` content is pinned to the parent epic's stated mapping. Reordering breaks `5wk.5` (seeder reads slot N → language). Header comment in the file declares the contract; any future edit requires a coordinated change with the seeder.
- **No API contract changes, no migrations, no consumers broken** — script is greenfield under `scripts/`.
