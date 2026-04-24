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

## Build iter 2 — responses to critic feedback

- **Decision (fix, blocking)**: Changed `inbox()` to pass `--label` (singular, repeatable) instead of `--labels`, added both `--label mail` and `--label mail-to:<agent>` for tight filtering, and added `--limit 0` to disable the 50-row cap.
  **Why**: Critic caught via e2e that `bd list` uses singular `--label` whereas `bd create` uses plural `--labels` — my iter 1 blindly reused `--labels` on both, so `inbox()` returned 0 mails against a real `bd`. 4 of 5 e2e tests were failing, including AC #3.
  **Alternatives considered**: `--label-any` (OR-semantics) — rejected; tight AND-filter keeps inbox scoped to actual mail addressed to this agent.

- **Decision**: Added `test_inbox_uses_singular_label_flag_and_unlimited` as a regression-lock test pinning the exact argv shape.
  **Why**: Unit tests previously couldn't catch the flag mismatch because `FakeBdBackend._flag` treated any string as opaque. Locking the flag names prevents a future edit from re-introducing the bug. Matches the broader "regression-lock argv whenever two sites share helpers" feedback pattern from the ClaudeCliBackend review.
  **Alternatives considered**: Only rely on e2e (slow, bd-dependent); only make the fake backend stricter (still lets a reviewer edit the source without a failing unit test).

- **Decision**: Made `FakeBdBackend._list` AND-filter over every repeated `--label` flag (matches real `bd list` semantics).
  **Why**: Keeps the fake honest; if the wrapper ever drops the `mail-to:<agent>` label, unit tests notice.

- **Decision (nit #2)**: Did not force `from_agent` to be required; left it optional and the footer omitted when unset.
  **Why**: Some internal senders (system notifications, cron-scheduled nags) legitimately have no human-readable origin. `Mail.from_agent=None` is a clear signal, and downstream consumers can substitute `"unknown"` at display time. Forcing a value would push dummy strings into the tracker.
  **Alternatives considered**: Default `from_agent="system"` — rejected; hides missing attribution in a way that looks intentional.

- **Decision (nit #3)**: Expanded the docstring in `po_formulas/__init__.py` to acknowledge the still-unlanded `software_dev`/`epic`/`deployments` sibling modules referenced by the core entry points.
  **Why**: Reviewer asked for an honest note on the in-flight coupling. The mail helper does not depend on those modules, so shipping it first is safe.

- **Decision (nit #4)**: Added a header comment to `mail_prompt.md` pointing at `prefect_orchestration.templates.render_template` as the substitution mechanism for `{{role}}`.
  **Why**: Removes the Jinja-vs-Handlebars ambiguity the critic flagged; the project's own templater is the answer.
