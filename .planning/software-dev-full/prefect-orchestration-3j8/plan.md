# Plan: prefect-orchestration-3j8 — dolt-server backend by default

## Current state (audited)

- `.beads/metadata.json` already has `dolt_mode: "server"`, `dolt_database: "prefect_orchestration"`, `dolt_host: "127.0.0.1"`. Server is up: `.beads/dolt-server.pid`, `.beads/dolt-server.port` (33463), `.beads/dolt-server.log` present.
- `bd list` against the live server works (verified — returns issues including this one).
- Old embedded data archived at `.beads/embeddeddolt.bak-20260424-163830/`. `.beads/embeddeddolt/` directory still present (untracked).
- **AC1 (data migration) is already done.** Remaining work is AC2 (CLAUDE.md), AC3 (po doctor warn), AC4 (README prerequisites). Plus a small concurrency-correctness verification step for AC1.

## Affected files

- `prefect_orchestration/doctor.py` — add `check_beads_dolt_mode()` + register in `ALL_CHECKS`.
- `tests/test_doctor.py` (or new `tests/test_doctor_dolt.py`) — unit-test the new check (warn on embedded, ok on server, ok-when-no-rig).
- `CLAUDE.md` (project) — add a short "Beads backend" subsection under "Beads Issue Tracker" documenting the recommended `bd init --backend=dolt-server` invocation and why.
- `README.md` — add a "Prerequisites" subsection (or extend `## Install`) listing `dolt` alongside `uv tool install`, with one-line install hint and a pointer to dolt-server rationale.
- (Optional) `engdocs/` — short note on dolt-server rationale; only if it doesn't fit cleanly in README.

## Approach

### AC3 — `po doctor` check (`check_beads_dolt_mode`)
Add a new core check in `prefect_orchestration/doctor.py`:

- Resolve rig path: cwd by default. Look for `<cwd>/.beads/metadata.json`. If absent → `OK` ("no .beads/ in cwd; skipping") so the check is harmless outside rigs.
- Read JSON; tolerate parse errors with `WARN` ("`.beads/metadata.json` unreadable").
- If `dolt_mode == "server"` → `OK` with `"dolt-server (db=<dolt_database>, host=<dolt_host>)"`.
- If `dolt_mode == "embedded"` (or absent / any other value) → `WARN` with remediation: "Re-init with `bd init --backend=dolt-server` for concurrent po-run safety. Existing data: `bd dolt migrate` (see CLAUDE.md → Beads backend)."
- Append callable to `ALL_CHECKS` list at module bottom.

Status choice: `WARN` not `FAIL` — embedded-dolt still functions for solo runs, only blows up under concurrency. Per AC3 ("warn on embedded-dolt").

### AC2 — CLAUDE.md
Add a `### Backend (dolt-server)` subsection under `## Beads Issue Tracker`:
- One paragraph on why (concurrent `po run` flows, exclusive-lock failures under embedded).
- Exact recommended invocation: `bd init --backend=dolt-server --dolt-database=<rig-name>`.
- Note that `po doctor` will warn if a rig is on embedded.
- Note this rig already runs against dolt-server (port file at `.beads/dolt-server.port`).

### AC4 — README.md
Add a `## Prerequisites` section directly above `## Install`:
- `uv` (tool runner).
- `dolt` CLI on PATH — install via `curl -L https://github.com/dolthub/dolt/releases/latest/download/install.sh | bash` (or `brew install dolt`). Required because PO rigs default to dolt-server backend for concurrent flow safety.
- `bd` (beads) on PATH.
- `tmux` (optional, enables lurkable agent sessions).

Keep terse — link to CLAUDE.md for the dolt-server rationale.

### AC1 — verification only (already migrated)
Run `bd list` from two shells concurrently while a write happens to confirm no exclusive-lock errors. Document the result in the build's decision log.

## Acceptance criteria (verbatim)

(1) Current prefect-orchestration/.beads runs against dolt-server; 'bd list' + 'bd show' + concurrent 'bd update' from N shells work without single-writer errors;
(2) CLAUDE.md documents dolt-server as the default PO rig setup with the exact 'bd init' invocation;
(3) po doctor check warns on embedded-dolt;
(4) README lists dolt-server alongside 'uv tool install' in prerequisites.

## Verification strategy

| AC | How verified |
|---|---|
| 1 | `cat .beads/metadata.json` shows `dolt_mode=server`. Run `bd list` + `bd show prefect-orchestration-3j8` — both succeed. Concurrency test: spawn 4 background `bd update <id> --notes="probe-N"` against 4 distinct beads simultaneously; confirm zero "exclusive lock" errors in stderr (capture to a tempfile, assert empty). |
| 2 | `grep -A5 "Backend (dolt-server)" CLAUDE.md` shows the section + exact `bd init --backend=dolt-server …` invocation. |
| 3 | New unit test: invoke `check_beads_dolt_mode()` with cwd patched to (a) a temp dir containing `.beads/metadata.json` w/ `dolt_mode=embedded` → assert `Status.WARN`; (b) `dolt_mode=server` → `Status.OK`; (c) no `.beads/` → `Status.OK`. Also run `po doctor` in the live rig → row present, status OK (server). |
| 4 | `grep -i -A3 "## Prerequisites" README.md` includes `dolt` and `uv`. |

## Test plan

- **Unit**: `tests/test_doctor.py` (or new file) — three parametrised cases for `check_beads_dolt_mode` using `tmp_path` + `monkeypatch.chdir`. No real `bd` invocation needed (read-file only).
- **E2E**: not strictly required, but extend `tests/e2e/test_po_doctor_cli.py` (already touched) with one assertion that the `po doctor` table contains a row mentioning "beads dolt" (or whatever the check name resolves to). Best-effort — skip if it complicates the e2e setup.
- **Playwright / UI**: N/A (no UI).

## Risks

- **Concurrency probe risk**: writing scratch notes to real beads pollutes the tracker. Mitigation: use a single throwaway bead created+closed in the probe, or run probes against a temporary `.beads/` rig under `tmp_path` in a unit test instead of against the live rig. Prefer the latter.
- **`bd init --backend=dolt-server` flag verification**: triage flagged it. Plan documents the invocation but the build step should `bd init --help` to confirm the exact flag spelling before pasting into CLAUDE.md/README. If the flag differs (e.g. `--dolt-mode=server`), use what `bd` actually accepts.
- **README contract**: adding a Prerequisites section is additive; no consumers break. CLAUDE.md edits are docs-only.
- **doctor.py contract**: `ALL_CHECKS` is internal; appending a new check is non-breaking. The existing `tests/e2e/test_po_doctor_cli.py` enumerates expected row count — confirm and bump if needed.
- **No migrations / no API contract changes**: data migration already happened on disk. No code path consumes `dolt_mode` programmatically beyond the new check.

## Out of scope

- Supervisord/systemd unit to keep the dolt-server up across reboots (issue's DESIGN suggests it but doesn't make it an AC). `po doctor` warning is sufficient surface; the build step can leave a TODO note in `engdocs/` or a follow-up bead.
- Optional `po init-rig` verb (DESIGN calls it explicitly future).
- Changing default behaviour of `bd init` itself (lives in beads, not here).
