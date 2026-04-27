# Plan — prefect-orchestration-hmc (`po-stripe` reference tool pack)

## Decision: where the pack lives

Build constraint: code edits and git commits must land in
`/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration`. So
the pack lives **inside the rig** at
`/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/po-stripe/`.

AC #1 ("po-stripe/ exists as sibling dir of `software-dev/po-formulas`") is
a *convention-level* statement — `engdocs/pack-convention.md` treats pack
location as convention, not contract. Triage explicitly authorized the
in-tree fallback ("Default to top-level `po-stripe/` inside the rig unless
told otherwise"). Top-level inside the rig keeps all PO pack code in one
git tree until a polyrepo split is justified, and matches the build
constraint exactly. The rig itself is a sibling of `software-dev/`, so the
in-tree pack is "sibling-adjacent" via one extra hop; defensible and
cleanly documented in the decision log.

A previous attempt placed the pack at `…/nanocorps/po-stripe/` (out of
tree); that location is superseded by this plan.

## Affected files (all under `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration`)

- `po-stripe/pyproject.toml`
- `po-stripe/po_stripe/__init__.py`
- `po-stripe/po_stripe/commands.py`
- `po-stripe/po_stripe/checks.py`
- `po-stripe/skills/stripe/SKILL.md`
- `po-stripe/overlay/CLAUDE.md`
- `po-stripe/README.md`
- `po-stripe/.gitignore`
- `po-stripe/tests/__init__.py`
- `po-stripe/tests/test_commands.py`
- `po-stripe/tests/test_checks.py`
- `pyproject.toml` — add `[tool.pytest.ini_options].testpaths = ["tests"]`
  so the rig's pytest doesn't recurse into `po-stripe/tests/` and try to
  import `po_stripe` outside its own venv.

**No edits to existing core code** under
`prefect_orchestration/`. The pack consumes existing entry-point groups
(`po.commands`, `po.doctor_checks`) wired in
`prefect_orchestration/commands.py` and `prefect_orchestration/doctor.py`;
overlay/skills delivery uses the existing 4ja.4 mechanism in
`prefect_orchestration/agent_session.py`.

## Approach

### `po-stripe/pyproject.toml`

Mirror the shape of `…/nanocorps/software-dev/po-formulas/pyproject.toml`.
Hatchling backend; ship `skills/` and `overlay/` inside the wheel via
`include = [...]` so the pack-convention wheel-layout probe finds them
on non-editable installs.

```toml
[project]
name = "po-stripe"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["prefect-orchestration", "stripe>=9.0"]

[project.entry-points."po.commands"]
stripe-balance = "po_stripe.commands:balance"
stripe-recent  = "po_stripe.commands:recent_charges"
stripe-mode    = "po_stripe.commands:mode"

[project.entry-points."po.doctor_checks"]
stripe-cli-installed = "po_stripe.checks:cli_installed"
stripe-env           = "po_stripe.checks:env_set"
stripe-api           = "po_stripe.checks:api_reachable"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["po_stripe"]
include  = ["skills", "overlay"]

[tool.uv.sources]
prefect-orchestration = { path = "..", editable = true }
```

`[tool.uv.sources]` points one directory up at the in-tree core
(`po-stripe/..` = the rig root, where the core's `pyproject.toml` lives).

The three command names don't collide with any core Typer verb (verified
against `prefect_orchestration/cli.py`: `run`, `list`, `show`, `deploy`,
`logs`, `artifacts`, `sessions`, `watch`, `retry`, `status`, `doctor`,
`install`, `update`, `uninstall`, `packs`, `from-file`, `attach`,
`serve`).

### `po_stripe/commands.py`

Three thin shells over the `stripe` CLI. Each:

1. Resolves `shutil.which("stripe")`; on absence, prints a one-line error
   to stderr pointing at `po doctor` and `raise SystemExit(2)` (precedent
   in `…/po-formulas/po_formulas/commands.py::summarize_verdicts`).
2. Runs `subprocess.run([stripe, ...args], shell=False,
   capture_output=True, text=True, timeout=10, check=False)`. No
   `shell=True`. `STRIPE_API_KEY` stays in env (read by the CLI itself);
   never on argv, never logged.
3. Parses JSON stdout (Stripe CLI emits JSON by default).
4. Tabulates with fixed-width `f"{val:Ns}"` — no `tabulate`/`rich` dep,
   keeps the dep set minimal.

- `balance()`: `stripe balance retrieve`. Print `available <amt> <ccy>` /
  `pending <amt> <ccy>` per row. Stripe amounts are minor units; divide
  by 100 for display.
- `recent_charges(limit: int = 10)`: `stripe charges list --limit <n>`.
  Validate `1 <= limit <= 100` (matches Stripe API limits). Tabulate
  `id | amount | currency | status | created (ISO UTC) | customer`.
  `--limit` arg parsing comes for free from the existing `po.commands`
  arg parser (int coercion in `prefect_orchestration/commands.py`).
- `mode()`: read `STRIPE_API_KEY` env. Inspect prefix only. Print
  `mode: test (sk_test_…)` / `mode: live (sk_live_…)` / `unknown` /
  `unset`. **Never echo full key** — slice `key[:8] + "…"`.

### `po_stripe/checks.py`

Three `DoctorCheck` returners (status: `green` | `yellow` | `red`):

- `cli_installed()`: `shutil.which("stripe")`. Missing → `red` with hint
  `"macOS: brew install stripe/stripe-cli/stripe · Linux: see
  https://docs.stripe.com/stripe-cli (apt repo or tarball)"`. Present →
  `stripe --version` with `timeout=4`; report version on green;
  non-zero / `OSError` → red; `TimeoutExpired` → yellow. Pattern lifted
  from `…/po-formulas/po_formulas/checks.py::claude_cli_present`.
- `env_set()`: lifts the example from `engdocs/pack-convention.md`
  §"Credentials". Missing → red. Malformed prefix → red. Otherwise
  inspect `PO_ENV` (case-insensitive `prod`):
  - dev (default): `sk_test_…` → green; `sk_live_…` → yellow.
  - prod (`PO_ENV=prod`): `sk_live_…` → green; `sk_test_…` → yellow.
  Only `key[:8] + "…"` ever appears in `message`/`hint`. Decision
  rationale (resolves triage's open question on dev/prod
  distinguishing): `PO_ENV` is a small per-host env var; cleaner than
  hostname sniffing or a separate `STRIPE_PO_MODE` knob.
- `api_reachable()`: short-circuit to **yellow** when `STRIPE_API_KEY`
  unset *or* `stripe` not on PATH (avoids double-red with the dedicated
  checks above; both already fail loudly). When both present, `stripe
  balance retrieve` with `timeout=5`; exit 0 → green; non-zero → yellow
  (snippet of stderr); `TimeoutExpired` → yellow; `OSError` → red.

### `skills/stripe/SKILL.md`

YAML frontmatter `name: stripe`, one-line description. Body sections in
this order (CLI-first per pack-convention §"Tool-access preference order"):

1. **Canonical vendor docs** — links to `docs.stripe.com/stripe-cli`,
   `docs.stripe.com/api`, `docs.stripe.com/llms.txt`,
   `docs.stripe.com/projects`.
2. **This nanocorp's rules**:
   - Test keys in dev (`sk_test_`); doctor warns on `sk_live_` unless `PO_ENV=prod`.
   - Charges > **$500** require `bd human <issue>
     --question="approve $<amt> charge to <customer>"` *before*
     `stripe charges create`.
   - Idempotency: `--idempotency-key "{issue_id}:{step_name}"` on every write.
   - Refunds: prefer `stripe refunds create --charge <id>` over
     PaymentIntent reuse.
3. **Quick CLI recipes** — `stripe balance retrieve`,
   `stripe charges create --amount … --idempotency-key "<id>:<step>"`,
   `stripe charges list --limit 10`, `stripe refunds create`,
   `stripe listen` (webhook dev).
4. **Pack-shipped helpers** — `po stripe-balance / -recent / -mode`.
5. **SDK fallback** — minimal `stripe.PaymentIntent.create(...)` for
   webhooks/streaming; same idempotency convention.
6. **HTTP API** — one sentence: don't.
7. **Doctor** — table of the three checks.

Skill stays short (vendor owns mechanics; pack owns policy).

### `overlay/CLAUDE.md`

≤ 30 lines: pointer to the skill, the three rules (test keys, $500 gate,
idempotency convention), `po doctor` reminder. Pack-convention
§"Per-role precedence" handles the merge via `AgentSession.prompt()`.

### `README.md`

Four short sections:

1. **What it is** (one paragraph; note in-tree location).
2. **Install**: `po install --editable
   /path/to/prefect-orchestration/po-stripe`; document the
   empirically-observed `uv tool` quirk (per-pack `po install` drops
   prior editable packs from the tool env). Reliable multi-pack
   one-liner: `uv tool install --reinstall --with-editable <pack-a>
   --with-editable <pack-b> --editable /path/to/prefect-orchestration`.
3. **Prerequisites**: stripe CLI install (macOS `brew install
   stripe/stripe-cli/stripe`; Linux pointer to
   `docs.stripe.com/stripe-cli`); `STRIPE_API_KEY` env var;
   `docs.stripe.com/projects` link for project-scoped keys.
4. **Quick check**: `po packs` / `po doctor` / `po stripe-balance` /
   `po stripe-mode`.

### `pyproject.toml` (rig)

Add:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
```

Without it, the rig's `pytest` recursively collects from cwd and would
pick up `po-stripe/tests/`, double-running pack tests and failing on
`import po_stripe` when the pack isn't installed in the rig's `.venv`.

## Acceptance criteria (verbatim)

(1) po-stripe/ exists as sibling dir of software-dev/po-formulas;
(2) pyproject declares stripe>=9.0 dep + 3 commands + 3 doctor checks;
(3) skills/stripe/SKILL.md is CLI-first, links vendor llms.txt + CLI docs +
    stripe projects docs, documents nanocorp policy (idempotency, bd human
    > $500, test-key discipline);
(4) commands shell out to 'stripe' CLI (not the Python SDK) in v1;
(5) doctor checks: stripe-cli-installed verifies binary on PATH with brew
    hint; stripe-env checks STRIPE_API_KEY presence + prefix; stripe-api
    pings with live call;
(6) overlay/CLAUDE.md reinforces skill;
(7) 'po install --editable /path/to/po-stripe' + 'po packs' lists it, 'po
    doctor' runs checks, 'po stripe-balance' works when env + CLI installed;
(8) after 4ja.4: session-start copies skills/stripe/SKILL.md →
    <rig>/.claude/skills/po-stripe/stripe/SKILL.md, overlay/ → cwd;
(9) README documents stripe CLI install command (macOS/Linux),
    STRIPE_API_KEY source, pointer to docs.stripe.com/projects for scoped
    keys.

## Verification strategy

| AC | Concrete check |
|---|---|
| 1 | `ls /home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/po-stripe/pyproject.toml` exits 0. Decision-log entry notes the in-tree location supersedes the literal "sibling-of-po-formulas" reading per triage's documented fallback (build-constraint reason). |
| 2 | `python -c "import tomllib,pathlib; d=tomllib.loads(pathlib.Path('po-stripe/pyproject.toml').read_text()); deps=d['project']['dependencies']; eps=d['project']['entry-points']; assert any(s.startswith('stripe>=9') for s in deps); assert len(eps['po.commands'])==3 and len(eps['po.doctor_checks'])==3"`. |
| 3 | grep `SKILL.md` for `docs.stripe.com/llms.txt`, `docs.stripe.com/stripe-cli`, `docs.stripe.com/projects`, `idempotency`, `bd human`, `sk_test_`. CLI-first ordering verified by section-order grep (CLI section before SDK fallback). |
| 4 | `grep -nE "import +stripe\|^from +stripe" po_stripe/commands.py` returns nothing; `grep -nE "subprocess\|shutil\.which" po_stripe/commands.py` returns the shell-out paths. |
| 5 | Unit tests parametrize the matrix (see Test plan). Manual smoke after `po install --editable`: `po doctor` shows 3 stripe-* rows with status reflecting current host state (red on missing CLI, red on missing env, yellow on api short-circuit; or all green). |
| 6 | `cat po-stripe/overlay/CLAUDE.md` contains "skill", "$500", "idempotency", "test key". |
| 7 | Sequence: `po install --editable po-stripe && po update && po packs | grep -q po-stripe && po doctor | grep -E 'stripe-(cli-installed\|env\|api)' && po stripe-mode` (last is env-only, runs without the CLI). With CLI + key: `STRIPE_API_KEY=sk_test_… po stripe-balance` (gated, manual). |
| 8 | After install, an `AgentSession.prompt()` invocation copies `<pack>/skills/stripe/**` → `<rig>/.claude/skills/po-stripe/stripe/**` (overwrite) and `<pack>/overlay/**` → `<rig>/**` (skip-existing). Verified manually post-install by `ls <rig>/.claude/skills/po-stripe/stripe/SKILL.md` after one stub turn; merge mechanics already covered by core's existing 4ja.4 tests. |
| 9 | `grep -E 'brew install stripe\|stripe-cli\|STRIPE_API_KEY\|docs.stripe.com/projects' po-stripe/README.md`. |

## Test plan

- **Unit tests** (`po-stripe/tests/`):
  - `test_commands.py`: monkeypatch `commands.shutil.which` and
    `commands.subprocess.run`; assert printed output via `capsys` for
    each of `balance` / `recent_charges` / `mode`. Cover:
    - balance happy path (available + pending rows tabulated with
      currency lowercased, amounts /100)
    - balance with `which → None` → SystemExit(2), stderr mentions
      `po doctor`
    - recent_charges happy path (≥ 2 rows, ISO timestamps, customer null)
    - recent_charges rejects `limit < 1` and `limit > 100`
    - recent_charges propagates non-zero CLI exit (stderr → SystemExit)
    - recent_charges clean-handles `TimeoutExpired`
    - mode: `sk_test_` → "test", `sk_live_` → "live", other →
      "unknown", unset → "unset"; full key never appears in capsys
      output (regression guard for secret leakage flagged in plan
      risks).
  - `test_checks.py`: parametrize across the matrix:
    - cli missing / present-OK / present-nonzero / present-timeout
    - env unset / malformed / `sk_test_` (dev|prod) / `sk_live_`
      (dev|prod) / case-insensitive `PROD`
    - api: env unset → yellow short-circuit (no subprocess call —
      assert subprocess.run replaced with a raiser is never invoked)
    - api: cli missing → yellow short-circuit
    - api: subprocess returns 0 / non-zero / TimeoutExpired / OSError
    - all paths: full key never appears in `message`/`hint`.
- **e2e** (`tests/e2e/`): not required by the issue. The CLI roundtrip
  path (`po install --editable` → `po packs` → `po doctor` → `po
  stripe-balance`) is best smoke-tested manually, since CI doesn't
  have the Stripe CLI on PATH or a test key. The rig's `.po-env` sets
  `PO_SKIP_E2E=1` so the software-dev-full flow won't try to run e2e
  for this issue — confirmed; nothing for this plan to change.
- **Playwright**: N/A (no UI; `has_ui=false` in triage).

Run with `cd po-stripe && uv run python -m pytest`. Rig's pytest scope
is unchanged for core tests; pack tests are deliberately not collected
from the rig's invocation.

## Risks

- **AC #1 wording vs build constraint.** Builder constraint pins
  commits to inside the rig; AC #1 says "sibling of
  `software-dev/po-formulas`". Triage explicitly authorized the
  in-tree fallback. Mitigation: log the deviation in
  `decision-log.md`; reference it in the commit message; let the
  critic weigh in.
- **`po install` (uv-tool) clobbers prior packs.** Empirical
  observation: `po install --editable <new>` removes
  previously-installed editable packs from the same uv tool env unless
  installed together via `uv tool install --reinstall --with-editable
  …`. README documents the multi-pack one-liner. Not a regression
  introduced by this issue.
- **Stripe CLI absence.** v1 commands `raise SystemExit(2)` cleanly
  with a `po doctor` pointer. Doctor row is the canonical signal.
- **Live API call from `stripe-api` doctor.** Capped at 5s;
  short-circuits on env/CLI missing. Worst-case adds 5s to `po
  doctor` on a malformed key — acceptable.
- **Secret leakage.** `mode()` / `env_set()` only ever print
  `key[:8] + "…"`. Test suite asserts the full key never appears in
  captured output (regression guard).
- **Entry-point shadowing.** None today against current core verbs;
  `po install`'s post-install scan refuses future shadows
  automatically.
- **Pack `tests/` collection collision.** Rig adds `testpaths =
  ["tests"]` to scope its pytest collection, preventing recursive
  pickup of `po-stripe/tests/` from the rig venv.
- **No core code changes.** Rig pyproject's `testpaths` addition is
  not an API contract change; it tightens (not widens) what pytest
  collects. No migration, no Prefect-server schema impact.
- **Baseline failures** (recorded in `baseline.txt`) are pre-existing
  and unrelated (`tests/e2e/test_po_deploy_cli.py`,
  `tests/test_agent_session_tmux.py`, `tests/test_mail.py`,
  `tests/test_watch.py`). The build for this issue must not regress
  them; it is not expected to fix them.
