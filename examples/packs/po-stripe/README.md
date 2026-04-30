# po-stripe

First reference **tool pack** for `po` (Prefect Orchestration), conforming
to [`engdocs/pack-convention.md`](../engdocs/pack-convention.md).

Ships:

- `skills/stripe/SKILL.md` — Claude Code skill teaching agents how to use
  Stripe safely in the deployment's context.
- `overlay/CLAUDE.md` — agent-facing reinforcement copied into the rig
  cwd at session start.
- 3 `po.commands` — `po stripe-balance`, `po stripe-recent`, `po stripe-mode`.
- 3 `po.doctor_checks` — `stripe-cli-installed`, `stripe-env`, `stripe-api`.
- Python dep `stripe>=9.0` (SDK) for the rare webhook/streaming case;
  v1 commands shell out to the `stripe` CLI binary.

The pack is colocated inside the `prefect-orchestration` rig at
`prefect-orchestration/po-stripe/` (top-level sibling of the core
`prefect_orchestration/` package). When polyrepo split happens, this
moves to its own repo with no code changes.

## Install

```bash
po install --editable /path/to/prefect-orchestration/po-stripe
po update                         # refresh entry-point metadata
po packs                          # confirm po-stripe is listed
```

(The pack's command names — `stripe-balance`, `stripe-recent`,
`stripe-mode` — don't collide with any core `po` verb. `po install`'s
post-install scan refuses any pack that would shadow a core verb, so
this is enforced at install time.)

### Multi-pack install (uv-tool quirk)

`po install --editable <pack>` invokes `uv tool install` under the
hood. Installing one editable pack at a time can drop previously
installed editable packs from the same uv tool environment. To install
multiple packs together (the reliable form), use:

```bash
uv tool install --reinstall \
  --with-editable /path/to/po-stripe \
  --with-editable /path/to/po-formulas-software-dev \
  --editable     /path/to/prefect-orchestration
po update
po packs        # all three packs visible
```

## Prerequisites

### Stripe CLI

The pack does **not** install the Stripe CLI (per pack-convention,
no post-install hooks). Install it once per machine:

- **macOS**: `brew install stripe/stripe-cli/stripe`
- **Linux**: see https://docs.stripe.com/stripe-cli for the apt repo
  or tarball install (Debian/Ubuntu apt is the easiest path; the
  page lists the exact `apt-get` lines for the official repo).

`po doctor` runs `stripe-cli-installed` and surfaces a red row with
the install hint if the binary is missing.

### `STRIPE_API_KEY`

Export the env var from your secret store. Use a **test key**
(`sk_test_…`) in dev. Project-scoped keys are recommended — see
https://docs.stripe.com/projects for how to mint them. Set
`PO_ENV=prod` on prod hosts so the doctor's mode hygiene flips
(green on live, yellow on test).

```bash
export STRIPE_API_KEY=sk_test_…
```

The pack never logs the full key. `po stripe-mode` and `po doctor`
echo only `key[:8] + "…"`.

## Quick check

```bash
po packs                            # ↳ po-stripe listed
po doctor                           # ↳ 3 stripe-* rows
po stripe-balance                   # ↳ available + pending per currency
po stripe-recent --limit 5          # ↳ tabulated charges
po stripe-mode                      # ↳ "mode: test  (sk_test_…)"
```

## Skill + overlay delivery

`AgentSession.prompt()` (shipped in core via `4ja.4`) walks every
installed pack at session start and:

- copies `skills/stripe/**` → `<rig>/.claude/skills/po-stripe/stripe/**`
  (always overwrite — pack owns canonical content)
- copies `overlay/**` → `<rig>/**` (skip-existing — user files win)

So `po install --editable /path/to/po-stripe`, then any agent session in
any rig will have the Stripe skill and the reinforcement `CLAUDE.md`
available without further glue.

## What lives where

```
po-stripe/
├── pyproject.toml          stripe>=9.0 + 3 commands + 3 doctor checks
├── po_stripe/
│   ├── commands.py         stripe-balance / stripe-recent / stripe-mode
│   └── checks.py           cli_installed / env_set / api_reachable
├── skills/stripe/
│   └── SKILL.md            agent-facing skill (CLI-first)
├── overlay/
│   └── CLAUDE.md           reinforcement merged into rig cwd
└── README.md               this file
```

## Tests

```bash
cd po-stripe
uv run python -m pytest
```

Unit tests mock `shutil.which` and `subprocess.run`; no live Stripe
calls in the test suite.
