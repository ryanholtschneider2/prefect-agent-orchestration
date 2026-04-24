# Plan: `po sessions <issue-id>` — list per-role Claude session UUIDs

## Affected files

- `prefect_orchestration/sessions.py` — **new**. Pure helpers: load metadata.json,
  parse `session_<role>` keys, enrich with last-iter/last-updated by scanning
  known role→artifact globs in the run_dir, and render a table.
- `prefect_orchestration/cli.py` — add `sessions` Typer command that resolves
  the run dir via `_run_lookup.resolve_run_dir`, calls the helpers, and
  handles the `--resume <role>` flag (emits a `claude --print --resume <uuid>
  --fork-session` one-liner to stdout).
- `tests/test_sessions.py` — **new**. Unit tests for the helpers + Typer
  command (table render, --resume one-liner, missing metadata.json, unknown
  role, no session for role).

## Approach

The `software-dev-full` pack writes `metadata.json` at the run_dir root with
flat string keys, including `session_<role> = <uuid>` for each role that has
run (confirmed on `.planning/software-dev-full/prefect-orchestration-5i9/metadata.json`:
`session_triager`, `session_builder`, `session_critic`, `session_verifier`,
`session_linter`, `session_tester`, `session_releaser`, `session_cleaner`,
`session_documenter`). There is **no** per-role iter / last-updated field
stored directly, so we derive them from artifact files in the run_dir:

| role        | artifact glob                      | iter source              |
|-------------|------------------------------------|--------------------------|
| triager     | `triage.md`                        | n/a (no iter)            |
| planner     | `plan.md`, `plan-critique-iter-*.md` | filename iter when present |
| builder     | `build-iter-*.diff`                | filename iter            |
| critic      | `critique-iter-*.md`               | filename iter            |
| verifier    | `verification-report-iter-*.md`    | filename iter            |
| linter      | `lint-iter-*.log`                  | filename iter            |
| tester      | `unit-iter-*.log`, `e2e-iter-*.log`| filename iter (max)      |
| releaser    | `decision-log.md`                  | n/a                      |
| cleaner     | `lessons-learned.md`               | n/a                      |
| documenter  | `final-tests.txt` (best-effort)    | n/a                      |

Unknown roles surfaced in metadata (future-proofing) are shown with `last-iter`
= `-`, `last-updated` = mtime of `metadata.json`. When the mapping has no
matching file, fall back to `metadata.json` mtime. Keep the role→glob table as
a module-level dict so it's easy to extend.

Command shape:

```
po sessions <issue-id>                    # prints table
po sessions <issue-id> --resume <role>    # prints single one-liner
```

Reuse `_run_lookup.resolve_run_dir(issue_id)` — same error path as `po logs`.
When the run dir exists but `metadata.json` is missing → exit code 3 with a
clear message (mirrors `po logs --file` missing-file behavior).

Table render reuses the same width-based formatter style as
`_print_deployment_table` in `cli.py` to stay consistent. Columns:
`ROLE | UUID | LAST-ITER | LAST-UPDATED`. `LAST-UPDATED` formatted as local
ISO-8601 seconds (e.g. `2026-04-24 12:16:02`). Rows sorted by role name.

`--resume <role>` looks up `session_<role>` in metadata; if present, prints:

```
claude --print --resume <uuid> --fork-session
```

…exactly once to stdout with no surrounding text (pipe-friendly). If the role
is not in metadata, exit 4 with `no session recorded for role {role!r}`.

Principles compliance: §1 (wraps something Prefect doesn't know about — bead
metadata + run_dir artifact layout); §2 (CLI-first, read-only, no new state).

## Acceptance criteria (verbatim)

1. prints table: role | uuid | last-iter | last-updated
2. `--resume <role>` emits a ready-to-run `claude --print --resume <uuid> --fork-session` one-liner
3. error if metadata.json missing

## Verification strategy

- **AC1**: unit test seeds a fake run_dir with a `metadata.json` containing
  three `session_*` keys plus dated artifact files, invokes
  `po sessions <id>` via `CliRunner`, asserts stdout contains the header
  `ROLE` / `UUID` / `LAST-ITER` / `LAST-UPDATED` and one row per session with
  the expected uuid + iter number.
- **AC2**: unit test invokes `po sessions <id> --resume builder` and asserts
  stdout equals `claude --print --resume <uuid> --fork-session\n`; exit code 0.
- **AC3**: unit test seeds a run_dir *without* `metadata.json`, monkeypatches
  `resolve_run_dir` to return it, and asserts exit code 3 and an error message
  mentioning `metadata.json`.
- Plus: `--resume <role>` where role has no entry → exit 4 with clear error.
- Plus: `resolve_run_dir` raises `RunDirNotFound` → exit 2 (matches `po logs`).

## Test plan

- **Unit** (`tests/test_sessions.py`): all 5 cases above, using the same
  `CliRunner` + tmp_path + monkeypatch pattern as `tests/test_cli_logs.py`.
- No playwright (CLI only, `has_ui=false`).
- No new e2e test needed — the `po logs` e2e already covers the
  `resolve_run_dir` integration. If the critic insists, add a thin e2e that
  writes metadata.json directly, seeds bd metadata via `bd update
  --set-metadata`, and runs `po sessions` as a subprocess.

## Risks

- **Metadata schema drift**: the role→artifact-glob table is a private
  heuristic; new roles in the pack won't have iter/timestamps until added.
  Mitigated by surfacing *every* `session_*` key even when the mapping is
  unknown (with `-` placeholders).
- **`--fork-session` flag availability**: spec'd in the AC, so we emit it as-is.
  If the installed `claude` CLI doesn't support the flag the user will see a
  CLI error on paste — acceptable, not our problem, and verified by the AC
  wording.
- No migrations, no API contract changes, no breaking consumers — this is a
  new read-only verb.
