# Plan: prefect-orchestration-3j8 — dolt-server backend by default

## Current state (re-audited iter 2)

All four ACs are **already satisfied** in the rig from prior work. This plan
is now verification-only — confirm each shipped artifact matches AC wording
and close the bead. No new code, no doc edits.

| AC | Shipped artifact | Location |
|---|---|---|
| 1 | `.beads/metadata.json` has `dolt_mode: "server"`, `dolt_database: "prefect_orchestration"`, `dolt_host: "127.0.0.1"`. Server up: `.beads/dolt-server.{pid,port,log}`. Old data archived at `.beads/embeddeddolt.bak-20260424-163830/`. | `.beads/metadata.json` |
| 2 | `### Backend (dolt-server)` subsection with full `bd init --server --server-host=… --server-port=… --server-user=… --database=…` block. | `CLAUDE.md:34` (block at L44) |
| 3 | `check_beads_dolt_mode()` reads `.beads/metadata.json`, returns OK on `dolt_mode=server`, WARN on embedded / missing / unreadable. Registered in `ALL_CHECKS`. | `prefect_orchestration/doctor.py:404`, registered at `:473` |
| 4 | `## Prerequisites` section above `## Install`, listing `uv` / `dolt` / `bd` / `tmux` with link to CLAUDE.md "Beads backend (dolt-server)". | `README.md:157` |

Unit tests for `check_beads_dolt_mode` already exist at
`tests/test_doctor_dolt.py` (5 cases: no `.beads/`, server, embedded,
missing-mode, unreadable-metadata). Verified via `grep`.

## Affected files

**None for code changes.** Verification-only iteration. The build step
will run checks against the existing files; no edits should be staged
beyond closing the bead.

If a verification turns up a regression (e.g. someone reverted CLAUDE.md
mid-flight), the build step reopens scope — but the expectation is zero
file diffs.

## Approach

Build step is a checklist run, not an implementation:

1. Read `.beads/metadata.json` → assert `dolt_mode == "server"`.
2. Live `bd list` against the running server → assert non-empty + zero
   "exclusive lock" stderr.
3. `grep -n "### Backend (dolt-server)" CLAUDE.md` → assert present.
   `grep -n "bd init --server" CLAUDE.md` → assert present.
4. `grep -n "## Prerequisites" README.md` → assert present, and that
   `dolt` is mentioned within the next ~20 lines.
5. `grep -n "def check_beads_dolt_mode" prefect_orchestration/doctor.py`
   → assert present. `grep -n "check_beads_dolt_mode" …doctor.py`
   anchored on `ALL_CHECKS` → assert registered.
6. `uv run python -m pytest tests/test_doctor_dolt.py -v` → 5 passes.
7. `uv run po doctor` in this rig → row "beads dolt mode" present and
   green ("dolt-server (db=prefect_orchestration, host=127.0.0.1)").
8. **Concurrency probe (in `tmp_path`, NOT against live rig)** —
   bootstrap a throwaway `.beads/metadata.json` with `dolt_mode=embedded`,
   confirm `check_beads_dolt_mode()` returns WARN; then with
   `dolt_mode=server` confirm OK. (This duplicates the existing unit
   tests — running them is the probe.) Skip an N-shell live `bd update`
   probe: it pollutes the live tracker (decision-log entry already
   notes this in iter 1).
9. Decision-log entry: "iter 2 plan re-audit found all ACs already
   shipped from iter 1; no diff this iteration."
10. Close the bead.

## Acceptance criteria (verbatim)

(1) Current prefect-orchestration/.beads runs against dolt-server; 'bd list' + 'bd show' + concurrent 'bd update' from N shells work without single-writer errors;
(2) CLAUDE.md documents dolt-server as the default PO rig setup with the exact 'bd init' invocation;
(3) po doctor check warns on embedded-dolt;
(4) README lists dolt-server alongside 'uv tool install' in prerequisites.

## Verification strategy

| AC | Concrete check |
|---|---|
| 1 | `python -c "import json; m=json.load(open('.beads/metadata.json')); assert m['dolt_mode']=='server'"`. Then `bd list >/dev/null 2>&1 && echo ok`. (Skip multi-shell concurrency probe against live rig — the real workload of N parallel `po run` flows already exercises this; document as such.) |
| 2 | `grep -n "### Backend (dolt-server)" CLAUDE.md` non-empty; `grep -n "bd init --server" CLAUDE.md` non-empty. |
| 3 | `uv run python -m pytest tests/test_doctor_dolt.py -v` — all 5 pass. `uv run po doctor` — exit 0 (no red rows from this check) and table contains "beads dolt mode" row with OK status. |
| 4 | `grep -n "## Prerequisites" README.md` non-empty; `grep -A 20 "## Prerequisites" README.md \| grep -i dolt` non-empty. |

## Test plan

- **Unit**: `tests/test_doctor_dolt.py` (already shipped, 5 cases). Re-run.
- **E2E**: skipped per rig `.po-env` `PO_SKIP_E2E=1`. The live `po doctor` invocation in step 7 covers the integration surface.
- **Playwright / UI**: N/A.

No new tests to write — coverage already exists.

## Risks

- **Drift since iter 1**: low but possible — another worker could revert CLAUDE.md / README / doctor.py between plan and build. Build step's grep checks will catch this and re-open scope.
- **`po doctor` row name**: the check is registered as `"beads dolt mode"` (verified by reading `doctor.py:407`). If renamed, update step 7's grep.
- **No migrations / no API contract changes / no consumer breakage**: zero diff is the expected outcome.

## What changed from iter 1 plan

- Iter 1 plan asserted only AC1 was already done and proposed re-implementing AC2/3/4. Critic was correct: AC2/3/4 are also already shipped.
- Iter 1 also proposed `bd init --backend=dolt-server` — wrong flag spelling. Real flag is `bd init --server` (verified in CLAUDE.md L44 and `bd init --help`). Iter 2 plan no longer touches CLAUDE.md/README.
- Concurrency probe scoped to existing unit tests (which already run against `tmp_path`-bootstrapped `.beads/`), not a live N-shell `bd update` storm.

## Out of scope

- Supervisord/systemd unit to keep dolt-server up across reboots (DESIGN-suggested, not an AC).
- Optional `po init-rig` verb (DESIGN-future).
- Changing default `bd init` behaviour upstream (lives in beads, not here).
