# Plan — prefect-orchestration-h9g

`po run --from-file <path/to/scratch.py> [--name <flow>] [args...]` — dispatch
an ad-hoc `@flow` function from a local Python file without packaging it as an
installed pack.

## Affected files

- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/cli.py`
  — add `--from-file` / `--name` options to `run()`, branch to a new loader.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/scratch_loader.py`
  *(new)* — `load_flow_from_file(path: Path, name: str | None) -> Flow`:
  importlib.util-based file loader, registers under a stable synthetic
  module name, locates `@flow`-decorated callables, errors helpfully on
  zero/multiple candidates without `--name`.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_cli_run_from_file.py`
  *(new)* — unit tests against the loader + a CLI `run` invocation using
  `CliRunner` and a temp `.py` file.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/e2e/test_po_run_from_file.py`
  *(new)* — subprocess-driven `po run --from-file` roundtrip with a real
  Prefect server reachable (skip otherwise).
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/skills/po/SKILL.md`
  — add a "Running an ad-hoc scratch flow" section.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/CLAUDE.md`
  — short bullet under "Common workflows" referencing `--from-file`.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/README.md`
  *(maybe)* — one-line mention next to the `po run` example.

## Approach

1. **`scratch_loader.load_flow_from_file(path, name)`** in a new module:
   - Resolve `path` against CWD; error cleanly if missing / not `.py`.
   - Build synthetic module name `po_scratch_<sha1(abspath)[:10]>`. If
     already in `sys.modules`, reuse (idempotent within one process).
   - Use `importlib.util.spec_from_file_location` + `module_from_spec`
     + `spec.loader.exec_module(module)`; insert into `sys.modules`
     *before* `exec_module` so internal `from __main__`-style refs work.
   - Walk module attributes, collect those that are Prefect flows by
     duck-typing on `prefect.flows.Flow` (import lazily). Returns the
     unique flow if `name is None`, else the named one.
   - Raise `ScratchLoadError` with a candidate list on ambiguity / 0 hits
     / unknown `--name`.

2. **`cli.run` signature change**:
   - Add `from_file: Path | None = typer.Option(None, "--from-file")`
     and `flow_name: str | None = typer.Option(None, "--name")`.
   - Make `name` argument optional (`...` → `None`); validate that
     exactly one of `name` / `from_file` is provided. Conflict rule
     from triage: explicit `--from-file` wins if both passed; warn the
     user. (Triage said "explicit beats implicit"; choose: error out if
     both are present to avoid silent surprise — clearer.)
   - When `--from-file`: call loader, then `flow_obj(**kwargs)` exactly
     like the registered path (same `_parse_kwargs(ctx.args)`, same
     `_autoconfigure_prefect_api()`, same `TypeError` → exit 2 handling,
     same `typer.echo(result)`).

3. **Backend selection / run-dir**: untouched. Scratch flows that don't
   take `issue_id` simply don't trigger `po logs/artifacts/watch`; this
   is documented, not enforced.

4. **No changes** to entry-point loading, `po list`, or `po show`. Per
   triage: scratch flows aren't registered, so they don't appear in
   `po list`; deferring `po show <path>` introspection.

## Acceptance criteria (verbatim)

(1) po run --from-file path/to/x.py invokes the @flow function in that
    file; (2) handles flows that take **kwargs from CLI like the
    existing po run does; (3) Prefect UI shows the run normally; (4)
    docs added to po skill + CLAUDE.md

## Verification strategy

- **AC1** — Unit: write a temp file with `@flow def hello(): return "hi"`,
  invoke `app` via Typer's `CliRunner` with `run --from-file <path>`,
  assert exit 0 and `"hi"` in stdout. E2E: same via subprocess `po`.
- **AC2** — Unit: temp file with `@flow def add(*, a: int, b: int): return a+b`,
  invoke `run --from-file ... --a 2 --b 3`, assert `5` in output.
  Confirms `_parse_kwargs` reuse and coercion.
- **AC3** — E2E (skip-if-no-server): with `PREFECT_API_URL` reachable,
  run a scratch flow and `prefect flow-run ls --limit 1` (or query
  client) shows a flow run with the scratch flow name. Asserts that
  Prefect's normal flow registration (decorator does it on import) is
  intact under our loader path.
- **AC4** — File presence check / grep in `skills/po/SKILL.md` and
  `CLAUDE.md` for the new section heading and `--from-file` token.

## Test plan

- **unit** (`tests/test_cli_run_from_file.py`): loader behavior
  (single-flow auto-detect, multi-flow → error with candidates,
  `--name` selection, missing file, non-`.py`, idempotent reload) +
  Typer `CliRunner` for the CLI wiring. Mock-free; `PREFECT_API_URL`
  unset so no server interaction (Prefect runs the flow locally).
- **e2e** (`tests/e2e/test_po_run_from_file.py`): real `po` subprocess
  on a temp scratch file; skip when `PREFECT_API_URL` is unreachable
  (other e2e tests do the same — match their skip pattern).
- **playwright**: N/A (no UI).

## Risks

- **Module name collisions**: if two scratch files share the synthetic
  name, second import would reuse cached module. Hash-of-abspath avoids
  that for distinct files; same file re-run is intentionally idempotent.
- **Import side effects**: arbitrary `.py` may run heavy code at import.
  Out of scope to sandbox — local single-user dev tool, document
  one-liner in skill.
- **Prefect Flow detection**: `isinstance(obj, prefect.flows.Flow)`
  relies on Prefect being importable in the same env (it is — PO depends
  on it). Lazy-import inside the loader so unit tests that don't touch
  the loader don't pay the cost.
- **Backend tmux session naming**: scratch flows lacking `issue_id` would
  break `TmuxClaudeBackend` if wired in. Not in this AC — scratch flows
  written by chief typically just call `subprocess`/`ClaudeCliBackend`.
  Leave to documentation: tmux backend assumes registered formulas.
- **No API contract changes** to existing `po run <name>` callers — the
  positional `name` becomes optional only when `--from-file` is passed;
  existing invocations keep working.
- **No migrations**, no breaking consumers.
