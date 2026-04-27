# Decision log — prefect-orchestration-hmc

## Build iter 1 (replan-only pass)

- **Decision**: No new code commits this iter; the in-rig pack already
  shipped at commit 9faa71e (`prefect-orchestration-hmc: po-stripe
  v0.1.0 — first reference tool pack (in-rig)`).
  **Why**: The plan was reverted on disk to the older sibling-dir
  version and re-staged with the in-rig version; the implementation
  already matches the in-rig plan. `uv run python -m pytest` in
  po-stripe passes 28/28. Re-committing the same files would create
  empty churn.
  **Alternatives considered**: Re-do the build from scratch — pure
  waste; the working tree is already consistent with the plan.

## Build iter 1 (initial — sibling-of-po-formulas attempt, superseded)

- Pack landed at `…/nanocorps/po-stripe/` (true sibling of
  `software-dev/po-formulas/`).
- Mode hygiene via `PO_ENV=prod` (sk_live → green when prod, yellow
  in dev; sk_test the inverse).
- `api_reachable` short-circuits to **yellow** (not red) when env or
  CLI is missing — avoids double-red with the dedicated checks.
- No `tabulate`/`rich` dep — fixed-width f-strings keep deps tight.
- Commands shell out via `subprocess.run([...], shell=False)`; SDK
  not imported in v1 per AC #4.
- 28 unit tests, mock-only — no live Stripe needed.
- Observed: `po install --editable <new>` (uv tool path) drops
  previously installed editable packs from the same env unless
  installed together with `uv tool install --reinstall
  --with-editable …`. Documented in README.

## Build iter 1 (re-do — in-rig location)

- **Decision**: Pack lives at
  `prefect-orchestration/po-stripe/` (inside the rig), superseding
  the prior sibling-dir attempt.
  **Why**: Build constraint pinned commits to inside the rig
  (`/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration`).
  Triage explicitly authorized the in-tree fallback. AC #1 says
  "sibling of `software-dev/po-formulas`" — the rig itself is a
  sibling of `software-dev/`, and engdocs/pack-convention treats
  layout as convention, not contract.
  **Alternatives considered**: Keep the sibling pack and skip the
  rig-side commit (rejected — violates explicit build constraint).
  Mark the issue blocked (rejected — the constraint is satisfiable
  with the documented fallback).

- **Decision**: Rig `pyproject.toml` gains
  `[tool.pytest.ini_options].testpaths = ["tests"]`.
  **Why**: Without it, the rig's `pytest` would recursively collect
  `po-stripe/tests/`, double-running pack tests and failing on
  `import po_stripe` when the pack isn't installed in the rig's
  `.venv`. This is the in-rig analogue of the plan's "pack tests
  collected separately" risk. Verified with `pytest --collect-only`:
  535 rig tests, no `po_stripe` test items.
  **Alternatives considered**: `norecursedirs = ["po-stripe"]`
  (works but more specific to one pack); `conftest.py` with
  `collect_ignore_glob` (more code for the same effect).

- **Decision**: `[tool.uv.sources] prefect-orchestration =
  { path = "..", editable = true }` in the in-rig pyproject — points
  one directory up at the rig root, where the core's pyproject.toml
  lives.
  **Why**: Plan §`po-stripe/pyproject.toml`. Mirrors the
  `software-dev/po-formulas` shape but with a one-up relative path
  instead of two-up.
  **Alternatives considered**: Absolute path (brittle across
  machines), no source (`pip install` would pull a published
  release; not what we want for in-tree dev).

- **Decision**: Files recreated fresh (not `cp -r` from the
  sibling) after the copy left a stray nested `.git` that the
  harness refused to remove and that would have polluted the rig
  tree.
  **Why**: Avoid sub-repo / submodule confusion. Cleaner history;
  the file contents are the same byte-for-byte (modulo path-only
  edits in pyproject and README).
  **Alternatives considered**: `git submodule add` of the sibling
  pack — over-engineered for a small pack, and inconsistent with the
  pack-convention's "any subset of optional features" model.

- **Decision**: README now documents the in-tree location and the
  `uv tool install --with-editable` multi-pack one-liner explicitly
  (the empirically-observed `po install --editable` quirk where each
  install drops prior editable packs).
  **Why**: Plan §"Risks" called this out as "not a regression
  introduced by this issue" but worth surfacing. Saves the next
  pack author a debugging cycle.
  **Alternatives considered**: Fix the `po install` behavior in
  core — out of scope for hmc; tracked separately.
