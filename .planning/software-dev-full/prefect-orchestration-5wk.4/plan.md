# Plan: prefect-orchestration-5wk.4 — snakes-demo rig provisioning script

## Affected files

- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/scripts/snakes-demo/provision-rig.sh` (new, executable)
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/scripts/snakes-demo/languages.txt` (new — canonical 100-language list, sourced into the rig's `engdocs/languages.txt`; lives in this repo so `5wk.5` seeder reads the same ordering)
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_snakes_demo_provision.py` (new — unit smoke test that runs the script into a tmpdir and asserts tree)

No changes to PO core, formulas, or runtime.

## Approach

Single bash script under `scripts/snakes-demo/` (new directory). Uses `set -euo pipefail`, strict shellcheck-style quoting, `[[ ]]` tests, `getopts`-style manual flag parsing for `--force` and `--remote <url>`. Behavior:

1. **Resolve target path**: `RIG_PATH="${RIG_PATH:-$HOME/Desktop/Code/personal/snakes-demo}"`.
2. **Idempotency gate**:
   - If `$RIG_PATH` exists and contains `.beads/` (our marker) → with `--force`, refuse if path is `$HOME` / empty / a parent of cwd, then `rm -rf -- "$RIG_PATH"`. Without `--force`, exit 1 with a clear "rig exists; use --force to wipe" message.
   - If `$RIG_PATH` exists but lacks `.beads/` → refuse unconditionally (don't risk wiping unrelated user data even with `--force`).
3. **Provision**:
   - `mkdir -p "$RIG_PATH"` then `cd "$RIG_PATH"`.
   - `git init -b main` (force initial branch name; falls back to `git init && git symbolic-ref HEAD refs/heads/main` for older git).
   - Resolve `git config user.name` / `user.email` from env (`GIT_AUTHOR_NAME`/`EMAIL`) → fallback to global git config; if neither is set, exit 1 with explicit message (don't silently commit with empty author).
   - Heredoc-write `README.md`, `CLAUDE.md`, `engdocs/languages.txt` (the languages.txt is copied verbatim from `scripts/snakes-demo/languages.txt` shipped beside the script — single source of truth for `5wk.5`).
   - `bd init` (embedded mode — single-tenant demo rig, no need for dolt-server; document choice in CLAUDE.md).
   - Optional `--remote <url>` → `git remote add origin <url>` (no push).
   - `git add -A && git commit -m "Initial snakes-demo rig"`.
4. **Locate self via `$BASH_SOURCE`** so the languages.txt sibling resolves regardless of cwd.

The CLAUDE.md content matches the issue description verbatim ("You are implementing the game Snake…") and adds a one-liner pointing to `engdocs/languages.txt` for the slot-to-language mapping.

`languages.txt` ships 100 lines, format `<N>\t<language>` (tab-separated), N=1..100. Picks a deterministic, well-known list (TIOBE-style top languages padded with esolangs/historical languages to reach 100) — content choice is "load-bearing" per triage but only insofar as it's stable; commit it once and `5wk.5` reads slot N.

## Acceptance criteria (verbatim from issue)

- Script idempotent.
- Resulting rig has `.git`, `.beads`, `README.md`, `CLAUDE.md`, `engdocs/languages.txt`, no `snakes/` yet.
- Lint passes (shellcheck strict mode).

## Verification strategy

- **Idempotent**: `tests/test_snakes_demo_provision.py` runs the script twice into a tmpdir — second invocation without `--force` exits non-zero with "rig exists" message; with `--force` succeeds and the resulting tree matches first run. Manual: `bash scripts/snakes-demo/provision-rig.sh` into `/tmp/foo` twice.
- **Resulting tree**: pytest asserts existence of `.git/`, `.beads/`, `README.md`, `CLAUDE.md`, `engdocs/languages.txt`, and absence of `snakes/`. Asserts `engdocs/languages.txt` has 100 non-empty lines.
- **Shellcheck strict**: CI / manual `shellcheck -S style scripts/snakes-demo/provision-rig.sh` exits 0. Run locally as part of the build step before declaring done.

## Test plan

- **unit** (`tests/test_snakes_demo_provision.py`): subprocess the script into a `tmp_path`, assert tree, assert idempotency (second run fails without `--force`, succeeds with), assert `--remote` adds an origin. Skip if `bd` or `git` not on PATH (CI has both per `po doctor`). Mocks nothing — but it's isolated to a tmpdir, no real network, no Prefect server, so it stays in `tests/` (unit layer per repo's CLAUDE.md test-layer rules).
- **e2e**: N/A — no PO CLI roundtrip.
- **playwright**: N/A — no UI.

Lint step: `shellcheck -S style scripts/snakes-demo/provision-rig.sh` invoked manually during build (lint-bug-fixer agent) and via the test that asserts shellcheck returns 0 (skipped if `shellcheck` not on PATH).

## Risks

- **Destructive `rm -rf`**: scoped behind `.beads/` marker check + refuse-if-path-equals-`$HOME`-or-`/` guard. Without `--force` the script never deletes. Tests cover both paths.
- **`bd init` mode**: embedded-dolt is the simpler choice for a single-tenant demo rig. Repo CLAUDE.md prefers dolt-server for parallel epics, but the snakes demo's parallelism is provided by PO across 100 child beads — concurrent `bd update` writes from parallel `po run`s in the *same* rig will hit dolt-embedded's exclusive lock. **Decision**: still go embedded for `5wk.4` (the script doesn't dictate run-time concurrency); document in CLAUDE.md that operators running 100 children in parallel should switch to `bd init --server` and start `dolt sql-server` out-of-band. `5wk.5` (seeder) and downstream runs can revisit if it bites.
- **Languages list immutability**: once committed, slot N → language must not shift, since `5wk.5` references N. Plan locks the file's ordering on first commit; future edits require a coordinated change with the seeder.
- **No API contract changes, no migrations, no consumers broken** — script is greenfield under `scripts/`.
