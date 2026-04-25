# Plan — prefect-orchestration-4ja.5

`po.commands` entry-point group: pack-shipped, non-orchestrated utility
ops dispatched as `po <command>` (NOT `po run`).

## Affected files

Core (`prefect-orchestration`):

- `prefect_orchestration/cli.py` — Typer dispatch hook for `po
  <command>` fallback; teach `list` to add a `kind` column; teach `show`
  to look up commands too.
- `prefect_orchestration/commands.py` *(new)* — small module: load
  `po.commands` entry points, dispatch a name to its callable, expose
  `CORE_VERBS` snapshot, and check for collisions vs core verbs.
- `prefect_orchestration/packs.py` — `po.commands` is already in
  `PACK_ENTRY_POINT_GROUPS`. Add post-install collision check inside
  `install()` / `update()`: after the `_run_uv` step, re-discover packs,
  scan `po.commands` EPs of any newly added pack, and if a name shadows
  a core verb raise `PackError` (and roll back via `uv tool uninstall`
  by re-running `update()` minus the offender — or simply error out and
  point the user at `po uninstall <name>` for clarity; see Risks).
- `tests/test_cli_commands.py` *(new)* — unit + Typer-runner tests for
  list/show/dispatch/collision.
- `tests/test_packs.py` — extend with a collision-rejection case.
- `tests/e2e/` — optional smoke covering `po summarize-verdicts` once
  pack ships it.
- `CLAUDE.md` — document the `po.commands` group under "Installed at
  runtime" / "When a task requires writing code here".

Pack (`../software-dev/po-formulas`):

- `po_formulas/commands.py` *(new)* — `summarize_verdicts(issue_id: str)`
  callable that resolves the run dir (reuse
  `prefect_orchestration.run_lookup.resolve_run_dir`) and prints a
  one-line summary per `verdicts/*.json`.
- `pyproject.toml` — add:

  ```toml
  [project.entry-points."po.commands"]
  summarize-verdicts = "po_formulas.commands:summarize_verdicts"
  ```

## Approach

1. **Discovery / dispatch in core**

   New `prefect_orchestration/commands.py`:
   - `load_commands() -> dict[str, Callable]` — mirrors
     `_load_formulas` but for `po.commands`.
   - `core_verbs() -> set[str]` — read off `cli.app` Typer command
     registry at import time (e.g. iterating
     `app.registered_commands`). Single source of truth, prevents drift.
   - `dispatch(name, extras) -> Any` — uses the existing
     `cli._parse_kwargs` helper to build kwargs and invokes the
     callable.

2. **CLI hook for `po <command>`**

   Typer doesn't natively support "fallback for unknown subcommand".
   Cleanest approach: keep all existing `@app.command()`s, then in the
   `if __name__ == "__main__":` block (and the `po` script entry point)
   replace `app()` with a small wrapper:

   ```python
   def main() -> None:
       argv = sys.argv[1:]
       core = commands.core_verbs()
       if argv and not argv[0].startswith("-") and argv[0] not in core:
           name = argv[0]
           registry = commands.load_commands()
           if name in registry:
               kwargs = cli._parse_kwargs(argv[1:])
               try:
                   result = registry[name](**kwargs)
               except TypeError as exc:
                   typer.echo(f"bad arguments for {name}: {exc}", err=True)
                   raise SystemExit(2)
               if result is not None:
                   typer.echo(result)
               return
       app()
   ```

   `pyproject.toml` already points the `po` script at
   `prefect_orchestration.cli:app`; switch to a new
   `prefect_orchestration.cli:main` (keep `app` for tests).

3. **`po list` `kind` column**

   Update `list_formulas()` (rename effect: still the `list` command)
   to load both `po.formulas` and `po.commands`, render rows like:

   ```
   KIND      NAME                MODULE:CALLABLE                 DOC
   formula   software-dev-full   po_formulas.software_dev:...    ...
   command   summarize-verdicts  po_formulas.commands:...        ...
   ```

   Sort by (kind, name). Empty case: still print the install hint.

4. **`po show <name>`**

   Look up in formulas first, then commands. If neither found, exit 1
   as today. For commands, signature is `inspect.signature(callable)`
   directly (no `.fn` Prefect unwrap).

5. **Install-time collision rejection**

   In `packs.install()` after `_run_uv` succeeds:
   - Re-discover packs and their contributions.
   - Scan `po.commands` EP names; intersect with `core_verbs()`.
   - If non-empty: raise `PackError` listing offending names and the
     pack, and tell the user `uv tool uninstall <pack>` (or run
     `po uninstall <pack>`) to roll back. We don't auto-uninstall —
     leaves the user in control and the failure mode is loud.

   `update()` performs the same check post-reinstall.

6. **Example pack command**

   `summarize_verdicts(issue_id)`:
   ```python
   def summarize_verdicts(issue_id: str) -> None:
       loc = resolve_run_dir(issue_id)
       vdir = loc.run_dir / "verdicts"
       if not vdir.is_dir():
           print(f"no verdicts/ under {loc.run_dir}")
           return
       for path in sorted(vdir.glob("*.json")):
           data = json.loads(path.read_text())
           verdict = data.get("verdict", "?")
           reason = (data.get("reason") or data.get("summary") or "").splitlines()[0:1]
           print(f"  {path.stem:24s}  {verdict:10s}  {reason[0] if reason else ''}")
   ```

## Acceptance criteria (verbatim from issue)

1. `po.commands` entry-point group documented in CLAUDE.md.
2. `po <command>` dispatches pack-registered callables.
3. `po list` shows both formulas and commands with `kind` column.
4. `po show <command>` prints callable signature + docstring.
5. Command name collision with core verbs rejected at install time.
6. Example command shipped in `po-formulas-software-dev` (e.g.
   `po summarize-verdicts <issue>`).

## Verification strategy

| AC | Check |
|----|-------|
| 1 | `grep -n po.commands CLAUDE.md` returns the new section. |
| 2 | Unit test: register a stub EP via `monkeypatch` of
      `commands.load_commands`, run `main(["mycmd", "--x=1"])`, assert
      the callable was invoked with `x=1`. |
| 3 | Unit test using Typer's `CliRunner` on `po list`: assert output
      contains both `formula` and `command` rows + a `KIND` header. |
| 4 | Unit test on `po show summarize-verdicts` (with stub registered):
      assert signature + docstring appear in stdout. |
| 5 | Unit test: stub `_run_uv` to succeed, monkeypatch
      `discover_packs` to return a pack whose `po.commands` includes
      `run`; assert `install()` raises `PackError` with the colliding
      name. |
| 6 | E2E (or unit on the pack): import
      `po_formulas.commands.summarize_verdicts`, point at a fake
      run_dir with two `verdicts/*.json`, capture stdout, assert both
      verdicts summarized. EP registration verified by reading the
      pack's `pyproject.toml`. |

## Test plan

- **Unit** (primary): `tests/test_cli_commands.py` covers AC2/3/4 via
  `typer.testing.CliRunner` + monkeypatched
  `commands.load_commands`. `tests/test_packs.py` extension covers
  AC5.
- **E2E** (light): existing `tests/e2e/` already shells `po`. Add a
  small test that installs the pack (or relies on the dev install
  already in place), runs `po list`, and greps for `command  summarize-verdicts`.
  Skip if `po` not on PATH.
- **No Playwright** — CLI only.

## Risks

- **Typer fallback**: Typer normally errors on unknown subcommands.
  The `main()` shim must run before Typer ever sees the argv. Mitigate
  by entry-point-pointing `po` script at `cli:main` and keeping `app`
  for tests. Verify with `uv pip show prefect-orchestration` for the
  console-script after a fresh install — and `po update` after this
  patch lands.
- **Core verb drift**: hard-coding the verb list would rot. Reading
  `app.registered_commands` keeps it auto-synced; covered by a unit
  test that asserts every `@app.command()` name is in `core_verbs()`.
- **Collision rollback policy**: erroring without auto-uninstall means
  a colliding pack stays installed but won't dispatch (core verb wins).
  Acceptable: failure is loud at install time, user can `po uninstall`.
  Document this explicitly in the error message.
- **Argument parsing parity**: reusing `cli._parse_kwargs` (same parser
  as `po run`) avoids drift. If `_parse_kwargs` ever changes, both code
  paths benefit.
- **Help discoverability**: `po --help` will not list pack commands.
  Mitigated by `po list`. Future enhancement (out of scope): inject
  pack commands into Typer dynamically.
- **No migrations / no API contracts**: pure CLI/EP wiring; nothing
  external breaks.
