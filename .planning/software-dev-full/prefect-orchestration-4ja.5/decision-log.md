# Decision log — prefect-orchestration-4ja.5 (build iter 1)

- **Decision**: Added a `cli.main()` shim and re-pointed the `po`
  console-script entry at it (was `cli:app`).
  **Why**: Typer doesn't natively support "fallback for unknown
  subcommand" — we have to inspect argv before Typer sees it. Keeping
  `app` exported for tests preserves the `CliRunner` test surface.
  **Alternatives considered**: a Typer `result_callback` on the root
  (only fires after a known subcommand resolves); a custom Click group
  subclass (more intrusive, would have to wrap every existing command).

- **Decision**: `core_verbs()` reads off `cli.app.registered_commands`
  rather than a hardcoded set.
  **Why**: prevents drift — every new `@app.command()` automatically
  joins the reserved set. The unit test
  `test_core_verbs_includes_all_typer_subcommands` cross-checks the
  expected set so additions are visible.
  **Alternatives considered**: a constant tuple in `commands.py` —
  rejected because it would silently rot.

- **Decision**: On collision the install/update verb raises
  `PackError`, but does not auto-uninstall.
  **Why**: failure is loud at install time; user keeps control of
  rollback (`po uninstall <pack>`). Auto-uninstall would surprise the
  user when uv exit codes are nonzero or when the user actually wants
  to fix the pack and reinstall in place.
  **Alternatives considered**: best-effort `uv tool uninstall <pack>`
  before raising; rejected for the surprise factor and because it
  doesn't work for editable installs cleanly.

- **Decision**: Reused `cli._parse_kwargs` from `po run` rather than
  reimplementing argv parsing.
  **Why**: argument parity with `po run` is a stated goal in the plan.
  Drift between the two would be confusing.
  **Alternatives considered**: a click-style ad-hoc parser inside
  `commands.dispatch` — rejected to avoid duplication.

- **Decision**: `commands.find_command_collisions` is a pure helper;
  `packs._check_command_collisions` is the wiring that calls
  `discover_packs()` and raises.
  **Why**: keeps the pure function unit-testable without monkey-patching
  the whole packs module.
  **Alternatives considered**: a single combined function in `packs`;
  rejected for testability and layering (commands module owns the
  reserved-verb concept, packs module owns lifecycle).

- **Decision (iter 2, response to critic nit #2)**:
  `_check_command_collisions()` re-scans **all** installed packs after
  every `install()` / `update()`, not just the pack whose contributions
  changed.
  **Why**: simpler, consistent semantics. A pre-existing collision in
  pack B will surface even when the user runs `po install <pack-A>`,
  which is a feature: any time the tool env mutates we re-validate the
  whole surface. If pack B was installed before this code shipped (or
  via a manual `uv tool install`), the next `po` lifecycle op flags it.
  **Alternatives considered**: scoping the check to "newly-added EPs
  for this call" — rejected because we'd need to snapshot pre-install
  state to diff, doubling the importlib.metadata round-trips for a
  marginal UX win.

- **Decision (iter 2, response to critic finding #1)**: Reverted the
  unrelated `tui` Typer command (and `_locate_po_tui` helper) that was
  swept into iter-1's commit. The `tui` line was also removed from the
  expected set in `test_core_verbs_includes_all_typer_subcommands`.
  **Why**: critic flagged it as scope creep — it's not in the plan,
  not in any AC, references binaries the repo does not ship, and was
  untested. It was already in the working tree when iter-1 began (cli
  was `M` before I touched it) and `git add prefect_orchestration/cli.py`
  swept it in. Stripping it now keeps this bead single-purpose.
  **Alternatives considered**: leaving it in and filing a follow-up
  bead — rejected, the critic explicitly asked for a revert and it
  belongs in its own change.

- **Decision**: Existing `test_install_invokes_uv_with_pack_spec` and
  `test_install_local_dir_becomes_editable` were updated to monkeypatch
  `discover_packs` to `[]`.
  **Why**: `install()` now invokes `_check_command_collisions()` after
  uv runs, which calls `discover_packs()`. Without the stub the tests
  would hit the real importlib.metadata and fail unpredictably in CI.
  **Alternatives considered**: making the collision check opt-in via a
  flag — rejected because that would defeat AC5 (must reject at install
  time, every time).
