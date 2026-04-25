# Decision log — prefect-orchestration-hmc (build iter 1)

- **Decision**: Pack landed at `…/nanocorps/po-stripe/` (true sibling of
  `software-dev/po-formulas/`), not inside the rig.
  **Why**: Plan §"Decision: where the pack lives" — write-test confirmed
  the sandbox allows it; AC #1 reads more naturally with a true sibling.
  **Alternatives considered**: `prefect-orchestration/po-stripe/` (intra-rig
  fallback) — rejected, no need.

- **Decision**: Mode hygiene uses `PO_ENV=prod` to flip the dev/prod
  expectation (sk_live_ → green, sk_test_ → yellow when `PO_ENV=prod`).
  **Why**: Triage flagged the open question on dev/prod distinguishing.
  Plan §`po_stripe/checks.py`. `PO_ENV` is a small surface, easy to set
  per host, doesn't depend on any other pack.
  **Alternatives considered**: hostname sniff (brittle), separate
  `STRIPE_PO_MODE` env (extra surface for one bit of info).

- **Decision**: `api_reachable` short-circuits to **yellow** (not red)
  when `STRIPE_API_KEY` is unset or the CLI is missing.
  **Why**: Both cases already have dedicated red checks
  (`stripe-env`, `stripe-cli-installed`); doubling the red here would
  fail `po doctor` twice for the same root cause. Yellow with a
  pointer to the underlying check is more legible.
  **Alternatives considered**: red — rejected as duplicative.

- **Decision**: No `tabulate` / `rich` dep for `recent_charges`. Plain
  fixed-width `f"{val:Ns}"` columns.
  **Why**: Plan §`po_stripe/commands.py` keeps deps tight to
  `stripe>=9.0`. Avoids a transitive dep just for a 6-column table.
  **Alternatives considered**: `tabulate` (extra dep), `rich` (heavy).

- **Decision**: Commands shell out via `subprocess.run([_BIN, ...args],
  shell=False)` and parse `stdout` as JSON.
  **Why**: Stripe CLI emits JSON by default; `shell=False` + arg-list
  form rules out shell injection. `STRIPE_API_KEY` is read by the CLI
  from env — never put on argv, never logged.
  **Alternatives considered**: importing the SDK directly — explicitly
  rejected by AC #4 ("commands shell out to 'stripe' CLI (not the
  Python SDK) in v1").

- **Decision**: Tests mock `shutil.which` and `subprocess.run`; no live
  CLI dependency. Test suite asserts the full key string never appears
  in printed output for the sk_test_/sk_live_/unknown paths in
  `mode()` and `env_set()` (regression guard for secret leakage flagged
  in plan risks).
  **Why**: Pack must be `pytest`-able on a CI host without `stripe`
  installed and without a real key. Plan §"Test plan".
  **Alternatives considered**: integration tests against `stripe`
  binary — out of scope for v1, would gate CI on a non-Python tool.

- **Decision**: When (re)installing po-stripe via `po install
  --editable`, the existing `po-formulas-software-dev` pack got dropped
  from the uv tool env (mutually-exclusive `--with-editable` semantics
  in the underlying `uv tool install`). Re-installed both together
  with `uv tool install --reinstall --with-editable <both> --editable
  <core>`.
  **Why**: Smoke verification (`po packs`, `po doctor`) needed both
  packs visible. Not a po-stripe regression — pre-existing `po install`
  behavior; flagged in `po doctor`'s `uv-tool install fresh` warning.
  **Alternatives considered**: leave po-formulas-software-dev
  uninstalled during smoke — would have skewed the regression-gate.
