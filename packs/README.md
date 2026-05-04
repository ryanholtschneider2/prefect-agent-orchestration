# Packs Workspace

This directory is the workspace for reusable PO packs that are not part of the
`prefect-orchestration` core package itself.

Intent:

- `prefect-orchestration/` stays focused on the runtime, CLI, entry-point
  contracts, scheduling, sessions, and shared infrastructure.
- Generic packs live under `packs/` next to the core repo because they are PO
  concepts, but they are not treated as core source files.
- Product-specific or nanocorp-specific packs should live with their owning
  repo, not here.

Current occupants:

- `po-formulas-examples/`
- `po-formulas-retro/`
- `software-dev-pack/`
- `software-dev-pack-wts/`
- `po-formulas-software-dev/`
- `po-formulas-software-dev-wts/`

Repo boundary:

- Treat the pack directories as a workspace area.
- Their own git history and packaging may evolve independently from the PO core.
- Update path references and editable-install docs when moving packs in or out
  of this directory.

If you are inventing a new pack, start with the pack convention in
`engdocs/pack-convention.md` and the concrete formula examples in
`engdocs/example-formulas.md` (`builder-heartbeat`, `triage-inbox`,
`on-bd-close`).
