# Plan — prefect-orchestration-hmc (`po-stripe` reference tool pack)

## Decision: where the pack lives

Per triage, AC #1 says "sibling dir of `software-dev/po-formulas`". The current
on-disk layout already has that pattern: this rig is at
`…/nanocorps/prefect-orchestration/`, and `…/nanocorps/software-dev/po-formulas/`
exists as a sibling. So **the pack lands at `…/nanocorps/po-stripe/`**, a true
sibling of `software-dev/po-formulas/`. Files created **outside** the rig are
still git-staged from inside their own tree (no remote yet).

If the build agent finds it can't write outside the rig (sandboxing), fall
back to `prefect-orchestration/po-stripe/` inside the rig and note the
deviation in the decision log; engdocs/pack-convention.md treats the location
as a convention, not a hard rule.

## Affected files

New pack `…/nanocorps/po-stripe/`:

- `po-stripe/pyproject.toml` — name `po-stripe`, deps `stripe>=9.0`, entry
  points for 3 commands + 3 doctor checks; hatchling build; wheel includes
  `po_stripe`, `skills/`, `overlay/` so the wheel-layout overlay probe works.
- `po-stripe/po_stripe/__init__.py` — empty.
- `po-stripe/po_stripe/commands.py` — `balance()`, `recent_charges()`, `mode()`.
- `po-stripe/po_stripe/checks.py` — `cli_installed()`, `env_set()`, `api_reachable()`.
- `po-stripe/skills/stripe/SKILL.md` — Claude Code skill (frontmatter + body).
- `po-stripe/overlay/CLAUDE.md` — agent-facing reinforcement of the skill.
- `po-stripe/README.md` — human-facing doc.
- `po-stripe/.gitignore` — minimal (`__pycache__`, `.venv`, `dist/`).

No core (`prefect-orchestration/`) changes are required. The pack is consumed
purely via the existing `po.commands` / `po.doctor_checks` entry-point groups
(both already wired in `prefect_orchestration/commands.py` and
`prefect_orchestration/doctor.py`) and the existing overlay/skills mechanism
(shipped in `4ja.4`, already merged).

## Approach

### `pyproject.toml`

Mirror the shape of `software-dev/po-formulas/pyproject.toml`. Hatchling
backend, `[tool.hatch.build.targets.wheel] packages = ["po_stripe"]`, plus
`include = ["skills/", "overlay/"]` so the pack-convention "wheel layout"
overlay probe (`<dist-root>/overlay/`) finds them when installed
non-editably. Editable installs find them next to `pyproject.toml` either way.

```toml
[project.entry-points."po.commands"]
stripe-balance = "po_stripe.commands:balance"
stripe-recent  = "po_stripe.commands:recent_charges"
stripe-mode    = "po_stripe.commands:mode"

[project.entry-points."po.doctor_checks"]
stripe-cli-installed = "po_stripe.checks:cli_installed"
stripe-env           = "po_stripe.checks:env_set"
stripe-api           = "po_stripe.checks:api_reachable"
```

The three command names don't collide with any core Typer verb (`run`,
`list`, `show`, `deploy`, `logs`, `artifacts`, `sessions`, `watch`, `retry`,
`status`, `doctor`, `install`, `update`, `uninstall`, `packs`) — verified
against `cli.py` registration. Document the absence-of-collision in the README.

### `po_stripe/commands.py`

Three thin shells over the `stripe` CLI. Each:

1. Resolves `shutil.which("stripe")` first; if missing, prints a one-line
   error pointing at `po doctor` and `raise SystemExit(2)` (consistent with
   `summarize_verdicts` precedent).
2. Runs `subprocess.run([...], capture_output=True, text=True, timeout=10,
   check=False)`. No shell=True, args list only (avoids injection).
3. Parses JSON output (Stripe CLI emits JSON by default) and prints a
   tabulated row set.
4. Never logs `STRIPE_API_KEY`. The CLI reads it from env; we don't pass it
   on argv.

- `balance() -> None`: `stripe balance retrieve`. Print available + pending
  per-currency: `available <amt> <ccy>` / `pending  <amt> <ccy>`. Amounts in
  Stripe are minor units (cents); divide by 100 for display.
- `recent_charges(limit: int = 10) -> None`: `stripe charges list --limit
  <n>`. Tabulate `id | amount | currency | status | created (ISO) |
  customer`. Use a fixed-width `f"{...:24s}"` print (no `tabulate` dep —
  keep deps tight to `stripe>=9.0`).
- `mode() -> None`: read `STRIPE_API_KEY` env var. Inspect prefix only.
  Print `mode: test (sk_test_…)` / `mode: live (sk_live_…)` / `unknown`.
  **Never echo the full key.** Slice `key[:8] + "…"`.

`recent_charges` accepts `--limit N` via the existing `po.commands` arg
parser (`prefect_orchestration/commands.py` already coerces ints).

### `po_stripe/checks.py`

Three `DoctorCheck` returners:

- `cli_installed()`: `shutil.which("stripe")`. Missing → `red` with hint
  `brew install stripe/stripe-cli/stripe  (macOS)  |  see
  https://docs.stripe.com/stripe-cli for Linux`. Present → run
  `stripe --version` with `timeout=4`; report version on green; treat
  non-zero / `OSError` as red. Pattern lifted from `claude_cli_present()`
  in po-formulas.
- `env_set()`: lifts the example from `engdocs/pack-convention.md` §
  "Credentials" verbatim. Missing → red. Malformed prefix → red. `sk_test_`
  → green. `sk_live_` → **yellow** with hint `live key in dev — set
  PO_ENV=prod to silence` (resolves triage open question on dev/prod
  distinguishing); if `os.environ.get("PO_ENV") == "prod"`, `sk_live_` is
  green and `sk_test_` is yellow ("test key in prod env"). Either way only
  `key[:8] + "…"` ever appears in `message`.
- `api_reachable()`: short-circuits to yellow ("STRIPE_API_KEY unset") when
  env is missing — never prompts, never asks for input. When set, runs
  `stripe balance retrieve` with `timeout=5`. Exit 0 → green; non-zero or
  timeout → yellow (transient-network-or-auth, hint to re-check key);
  `OSError` → red ("stripe CLI missing — see stripe-cli-installed").

### `skills/stripe/SKILL.md`

YAML frontmatter:

```
---
name: stripe
description: Charge customers, issue refunds, inspect balances via Stripe — CLI-first.
---
```

Body sections (markdown), in this order:

1. **Canonical vendor docs** — links to
   `https://docs.stripe.com/stripe-cli`, `https://docs.stripe.com/api`,
   `https://docs.stripe.com/llms.txt`, `https://docs.stripe.com/projects`.
2. **This nanocorp's rules**:
   - Test keys in dev (`sk_test_`); doctor warns on `sk_live_` unless
     `PO_ENV=prod`.
   - Charges > **$500** require `bd human <issue> --question="approve $<amt>
     charge to <customer>"` and a recorded human response *before* the
     `stripe charges create` call.
   - Idempotency convention: every write call passes
     `--idempotency-key "{issue_id}:{step_name}"` (or
     `idempotency_key=...` on the SDK fallback).
   - Refund flow: prefer `stripe refunds create --charge <id>` over
     re-using a PaymentIntent.
3. **Quick CLI recipes** (CLI is tier 1 per pack-convention §"Tool-access
   preference order"): `stripe balance retrieve`, `stripe charges create
   --amount 2000 --currency usd --source tok_visa --idempotency-key
   "<id>:<step>"`, `stripe charges list --limit 10`, `stripe refunds
   create --charge ch_…`.
4. **SDK fallback** — for webhooks/streaming/typed responses; one minimal
   `stripe.PaymentIntent.create(...)` example with the same idempotency
   convention. Marked clearly as fallback.
5. **HTTP API** — one sentence: don't, the CLI and SDK cover everything.
6. **Doctor** — pointer to `po doctor` for prerequisite checks.

Skill kept short — vendor owns mechanics (per pack-convention §
"Official vendor skills / llms.txt — link, don't duplicate").

### `overlay/CLAUDE.md`

Short reinforcement (≤ 30 lines): "If you're touching Stripe, read
`.claude/skills/po-stripe/stripe/SKILL.md` first. The three rules that
matter: test keys, $500 human gate, idempotency keys derived from
`{issue_id}:{step_name}`. Run `po doctor` before any first call."

Pack-convention §"Per-role precedence" handles overlay merging via
`AgentSession.prompt()`; we just author the file.

### `README.md`

Human-facing, four short sections:

1. What it is (one paragraph).
2. **Install**: `po install --editable /path/to/po-stripe` then `po
   update` if entry points were edited.
3. **Prerequisites**:
   - Stripe CLI: `brew install stripe/stripe-cli/stripe` (macOS); for
     Linux, link to `https://docs.stripe.com/stripe-cli` with the
     specific tarball-or-apt instruction.
   - `STRIPE_API_KEY` env var; pointer to
     `https://docs.stripe.com/projects` for project-scoped keys.
4. **Quick check**: `po packs` → see `po-stripe`; `po doctor` → 3 rows;
   `po stripe-balance` → balance.

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
| 1 | `ls /home/ryan-24/Desktop/Code/personal/nanocorps/po-stripe/pyproject.toml` exits 0; sibling of `…/nanocorps/software-dev/po-formulas/`. |
| 2 | `grep -E 'stripe>=9\.0' po-stripe/pyproject.toml`; `python -c "import tomllib,pathlib; d=tomllib.loads(pathlib.Path('po-stripe/pyproject.toml').read_text()); eps=d['project']['entry-points']; assert len(eps['po.commands'])==3 and len(eps['po.doctor_checks'])==3"`. |
| 3 | grep `SKILL.md` for `docs.stripe.com/llms.txt`, `docs.stripe.com/stripe-cli`, `docs.stripe.com/projects`, `idempotency`, `bd human`, `sk_test_`. CLI-first ordering verified by section order grep. |
| 4 | `grep -n "import stripe\|stripe\." po_stripe/commands.py` returns no SDK calls; `grep -n "subprocess\|shutil.which" po_stripe/commands.py` returns the shell-out paths. |
| 5 | Unit tests below; `po doctor` integration smoke after `po install --editable`. |
| 6 | `cat po-stripe/overlay/CLAUDE.md` shows reinforcement text mentioning skill, $500 gate, idempotency, test keys. |
| 7 | Run end-to-end: `po install --editable …/po-stripe && po update && po packs | grep po-stripe && po doctor | grep -E 'stripe-(cli-installed|env|api)' && STRIPE_API_KEY=sk_test_… po stripe-balance` (last requires fixture key, gated). |
| 8 | After installing into a rig, run a stub `AgentSession.prompt()` (or check `<rig>/.claude/skills/po-stripe/stripe/SKILL.md` exists post-`prompt()` call) — covered by overlay-merging tests already in core (4ja.4). Add a smoke step in the README. |
| 9 | `grep -E 'brew install stripe|stripe-cli|STRIPE_API_KEY|docs.stripe.com/projects' po-stripe/README.md`. |

## Test plan

- **Unit tests** (`po-stripe/tests/`):
  - `tests/test_commands.py`: monkeypatch `shutil.which` to return `/usr/bin/stripe`,
    monkeypatch `subprocess.run` to return canned JSON for each of the three
    commands. Assert printed output (capsys). Also assert behavior when
    `which` returns None (SystemExit(2), error message references `po doctor`).
  - `tests/test_checks.py`: parametrize across:
    - cli missing (`which → None`) → red with brew hint
    - cli present, version OK → green
    - env unset → red
    - env malformed → red
    - env `sk_test_…`, `PO_ENV` unset → green
    - env `sk_live_…`, `PO_ENV` unset → yellow
    - env `sk_live_…`, `PO_ENV=prod` → green
    - api: env unset → yellow short-circuit (no subprocess call)
    - api: subprocess returns 0 → green
    - api: subprocess returns non-zero → yellow
    - api: `OSError` from subprocess → red
- **e2e**: not required by the issue. The CLI roundtrip path
  (`po install --editable` → `po packs` → `po doctor` → `po
  stripe-balance`) is best smoke-tested manually as documented in the
  README; an automated e2e test would require `stripe` on PATH and a live
  test key, which CI doesn't have.
- **Playwright**: N/A (no UI).

Tests run via `cd po-stripe && uv run python -m pytest`. The pack ships
its own `pyproject.toml` `[tool.uv.sources]` pointing at the editable
core (mirroring `po-formulas`).

## Risks

- **Sibling-dir creation outside the rig.** The build sandbox may refuse
  writes to `…/nanocorps/po-stripe/`. Fallback: place the pack at
  `prefect-orchestration/po-stripe/` inside the rig and note the
  deviation. Either location satisfies "sibling of
  `software-dev/po-formulas/`" only loosely — the engdocs treat layout as
  convention. (Triage flagged this.)
- **Stripe CLI absence on dev machines.** `po install` succeeds without it;
  `po doctor` is the loudspeaker. Not a regression risk for core tests, since
  no core test imports `po_stripe`.
- **Live API call from `stripe-api` doctor.** With `timeout=5` and short-
  circuit on missing env, this can't hang `po doctor`. Still: if a user
  has a malformed key, doctor will spend up to 5s on this row. Acceptable.
- **Secret leakage.** Tested explicitly: `mode()` and `env_set()` only ever
  print `key[:8] + "…"`. Adding `key` to a log line is the regression to
  guard against — covered by an assertion in `test_commands.py` and
  `test_checks.py` (capsys output never contains the test key in full).
- **Entry-point shadowing.** None today; if a future core verb collides
  with `stripe-balance` / `stripe-recent` / `stripe-mode`, `po install`'s
  post-install scan will refuse, per CLAUDE.md "Collision handling" para.
- **No core changes** — so no API contract breakage, no migrations, no
  Prefect-server-side schema impact.
- **Baseline failures** (recorded in `baseline.txt`) are pre-existing and
  unrelated (mail prompt path, watch test, tmux session derivation,
  deploy CLI). The build for this issue must not regress them, but is
  not expected to fix them either.
