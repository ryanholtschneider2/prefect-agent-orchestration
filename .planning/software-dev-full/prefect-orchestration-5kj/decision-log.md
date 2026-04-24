# Decision log — prefect-orchestration-5kj (build iter 1)

- **Decision**: Encoded mail as `--type=task --labels=mail,mail-to:<to>` rather than the `--type=message` shape in the issue description.
  **Why**: `bd create --type=` only accepts `bug|feature|task|epic|chore|decision` unless `types.custom` is configured (plan Risks §1; verified via `bd create --help`). Labels are zero-config and filterable via `bd list --labels=`.
  **Alternatives considered**: Requiring users to set `types.custom = message` in beads config; encoding via title prefix only (fails: no cheap filter on bd side).

- **Decision**: Created top-level `po_formulas/` package in this repo.
  **Why**: Issue AC #1 mandates the module path `po_formulas/mail.py`, and `cli.py`/`deployments.py` already reference `po_formulas.*` entry points that do not yet exist in-tree. Plan "Context & decisions" bullet 1.
  **Alternatives considered**: Putting the module in `prefect_orchestration.mail` and shimming `po_formulas.mail` via a re-export — rejected because the plan's path needs to be authoritative and this is the cleanest seed for the sibling formulas.

- **Decision**: Mail priority set to `4` (backlog) on every `send()`.
  **Why**: Plan Risks §3: prevents mail from surfacing in `bd ready` as real work.
  **Alternatives considered**: Leaving default priority and relying on dashboards filtering labels=mail out — weaker isolation.

- **Decision**: `send()` raises `RuntimeError` when `bd` is missing, rather than no-op.
  **Why**: Silent mail drops are a nasty failure mode — a sender that thinks it delivered when nothing was sent breaks the whole protocol. `inbox()` and `mark_read()` still no-op, matching the reader-side tolerance pattern in `prefect_orchestration.beads_meta`.
  **Alternatives considered**: Uniform no-op for both sides (too dangerous); raise on all sides (noisy in read paths where absence of bd just means "no mail").

- **Decision**: Prompt fragment shipped as a separate `mail_prompt.md` file + a note in `AGENTS.md`, rather than editing builder/critic role prompts directly.
  **Why**: Those prompt files do not yet exist in this repo (no `prompts/` directory under `po_formulas/software_dev/`); see plan "Context & decisions" bullet 3. The fragment is the contract; callers include it.
  **Alternatives considered**: Scaffolding empty builder/critic prompt files just to host the fragment — deferred until the software-dev formula package lands.

- **Decision**: Demo test uses a `FakeBdBackend` that emulates `bd create/list/close` in memory.
  **Why**: Plan test plan: keeps the unit test hermetic and fast; a gated integration test against a real `bd` is left as a future follow-up (not required for AC).
  **Alternatives considered**: `unittest.mock.patch("subprocess.run")` with per-test canned responses — rejected; replaying the full send→inbox→close handoff (AC #3) needs shared state across calls, which the FakeBdBackend provides naturally.
