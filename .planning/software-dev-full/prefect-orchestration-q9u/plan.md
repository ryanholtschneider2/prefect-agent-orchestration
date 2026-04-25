# Plan: prefect-orchestration-q9u — `po-nanocorp-starter` meta-pack

## Scope clarification

The starter is a **standalone sibling pack** (like `po-formulas-software-dev`
at `../software-dev/po-formulas/`), not code in this repo. Per
`engdocs/separation.md` § "Starter meta-pack" the convention is one pack =
one repo. Since this rig has no git remote and there's no separate
`po-nanocorp-starter` checkout yet, we scaffold the pack at:

```
../po-nanocorp-starter/                  # sibling of prefect-orchestration/
├── pyproject.toml
├── README.md
├── overlay/
│   └── .claude/welcome-starter.md       # avoids CLAUDE.md collision (overlay skip-existing)
└── po_nanocorp_starter/
    ├── __init__.py
    ├── deployments.py
    ├── commands.py
    └── checks.py                        # optional starter-level health check
```

All file paths below are relative to that pack root unless prefixed `core:`.

## Affected files

**New (in `../po-nanocorp-starter/`)**
- `pyproject.toml` — name `po-nanocorp-starter`, declares `[project] dependencies` listing every curated pack, registers `po.deployments`, `po.commands`, `po.doctor_checks` entry points.
- `README.md` — env vars (STRIPE_API_KEY, GOOGLE_*, SLACK_*, ATTIO_*), install + uninstall instructions, "graceful degrade" caveat, links to underlying pack READMEs.
- `po_nanocorp_starter/__init__.py` — empty.
- `po_nanocorp_starter/deployments.py` — `register()` enumerates installed `po.formulas` entry points (skip `epic`, `software-dev-full`, anything tagged "meta") and produces a `retro-weekly-<formula>` `RunnerDeployment` per installed retro-capable formula. Failure-tolerant — wrap each EP load in try/except and log a warning.
- `po_nanocorp_starter/commands.py` — three callables wired via `po.commands`:
  - `spend(month: str | None = None)` — call into `po_stripe` if importable for MTD card spend, else print "po-stripe not installed". Add LLM/Logfire spend pull behind a try/except.
  - `inbox(limit: int = 20)` — call into `po_gmail` (or `po_attio`) for recent triaged messages; degrade to "no email pack installed".
  - `kpi()` — composite snapshot: spend totals + inbox count + last retro run timestamp; each section guarded by an optional-import block.
- `po_nanocorp_starter/checks.py` — one `DoctorCheck` (`starter_dependencies_present`) that lists which curated packs are installed vs missing; status `green` only if every dep present, `yellow` otherwise (informational, not red — graceful degrade is allowed).
- `overlay/.claude/welcome-starter.md` — short primer pointing at `po list`, `po doctor`, `po spend`, `po inbox`, `po kpi`. Uses `welcome-starter.md` (not `CLAUDE.md`) so it never collides with a rig's existing CLAUDE.md.

**No edits to `prefect-orchestration/` core** — discovery, doctor aggregation, command dispatch, and overlay copying are already pack-agnostic. We rely on `core:cli.py` `po.commands` discovery (existing), `core:doctor.py` pack-check aggregation (existing), and `core:pack_overlay.py` overlay copy (existing).

## Approach

1. **Scaffold the pack** at `../po-nanocorp-starter/` using the same layout as `software-dev/po-formulas/`. `pyproject.toml` mirrors `software-dev/po-formulas/pyproject.toml` structure.

2. **Curated dependencies** as `[project] dependencies` (hard deps so `po install po-nanocorp-starter` pulls everything in one shot — AC #5):
   ```toml
   dependencies = [
     "prefect-orchestration",
     "prefect>=3.0",
     "po-formulas-software-dev",
     # The following are aspirational — gate per existence today:
     # "po-formulas-retro",   # 7vy in_progress
     # "po-stripe",           # hmc in_progress
     # "po-gmail", "po-gcal", "po-slack", "po-attio",  # 3cu epic
   ]
   ```
   Per triage risk § "Hard vs soft dependencies": include only deps whose packs exist today; comment-block the rest with a TODO referring to the blocking bead. This keeps `po install po-nanocorp-starter` from failing for unresolvable refs while still satisfying AC #1 ("depends on the curated set" — best-effort given dependency-pack availability).
   - For the missing packs, document the manual `po install` follow-up in README.

3. **Default deployments (AC #2)** — `deployments.register()` walks `importlib.metadata.entry_points(group="po.formulas")`, skips `{"epic","software-dev-full"}` by default plus a `STARTER_RETRO_SKIP` constant, and produces one `Deployment.from_source(...)`-style retro deployment per remaining formula keyed `retro-weekly-<entry_point.name>`. Schedule: cron Sunday 09:00 UTC. Wrap entire body in try/except — never let registration explode the consumer's `po deploy`. Returns `[]` if no eligible formulas.

4. **Aggregator commands (AC #3)** — three modules exposing top-level callables. Each uses `importlib.import_module` inside a try/except to detect whether the underlying pack ships a callable named `mtd_spend()` / `recent_triaged()` / etc.; if absent, print a helpful "install `po-stripe` to enable" line and exit 0 (graceful degrade — AC #8). Argument parsing matches `po run` conventions described in CLAUDE.md (`--key value`, `--no-flag`).

5. **Welcome overlay (AC #4)** — `overlay/.claude/welcome-starter.md` (not `CLAUDE.md`). Per triage risk § "Welcome overlay collision" plus `pack_overlay.py` skip-existing semantics, putting our welcome in `.claude/welcome-starter.md` keeps it visible to Claude Code (which scans `.claude/`) without ever touching the rig's primary CLAUDE.md. Doc tells the user where to source it from in their CLAUDE.md if they want.

6. **Doctor aggregation (AC #7)** — already automatic in core; we add one starter-level check for "all curated packs present" so the user gets a single pane-of-glass row and a clear "yellow → run `po install`" hint. AC is satisfied by the existing core mechanism.

7. **Graceful degrade (AC #8)** — guaranteed by wrapping every cross-pack call in try/except `ImportError`/`PackageNotFoundError`. Test by uninstalling one pack and running `po spend`/`po doctor`/`po deploy --apply` — none should error.

8. **README (AC #9)** — sections: install (one liner), env vars (table per integration), uninstall + degrade contract, deployment overview, command reference. Link out to per-pack READMEs for credential-flow detail.

## Acceptance criteria (verbatim)

(1) pyproject depends on the curated set; (2) deployments.py registers retro-weekly-<target> per formula pack; (3) 3 aggregator commands; (4) welcome CLAUDE.md overlay; (5) po install starter installs everything via deps; (6) po packs shows all; (7) po doctor aggregates all checks; (8) graceful degrade on uninstall; (9) README covers env setup.

## Verification strategy

| AC | How verified |
|----|---|
| 1 | `grep -A 30 '\[project\]' pyproject.toml` confirms curated deps. Unit test reads `pyproject.toml`, asserts a known minimum set is listed in `dependencies` (or in a documented "aspirational" comment block for unbuilt packs). |
| 2 | Unit test installs the pack editably in a tmp env and calls `po_nanocorp_starter.deployments.register()` with a stub `entry_points` (monkeypatched), asserts one `RunnerDeployment` per stub formula and zero for `epic`/`software-dev-full`. |
| 3 | E2E test (subprocess): `po list` includes `spend`, `inbox`, `kpi`. `po spend --month 2026-04` runs and exits 0 (gracefully degraded if `po-stripe` absent — output contains "not installed" line). |
| 4 | Unit test: `pack_overlay.discover_packs()` finds `po-nanocorp-starter`, then `apply_overlay(pack, dest=tmp)` materializes `.claude/welcome-starter.md`. Test that re-applying skips the existing copy. |
| 5 | E2E test: `po install --editable ../po-nanocorp-starter` succeeds; `po packs` lists the starter and at least one transitive dep (`po-formulas-software-dev`). |
| 6 | Same E2E as #5 — `po packs` table contains starter row plus its deps. |
| 7 | E2E test: `po doctor` exit 0 on healthy env; assert table contains the starter's `starter_dependencies_present` row alongside core checks. |
| 8 | E2E test: install starter, `po uninstall po-formulas-software-dev` (or skip if not installed in test fixture); re-run `po spend`/`po inbox`/`po kpi` and `po doctor` — none should crash, doctor row turns yellow with hint text. |
| 9 | Lint test: README contains H2 sections for `Installation`, `Environment variables`, `Commands`, `Deployments`, `Uninstall`, and references each curated env var name (STRIPE_API_KEY, etc.). Quick grep test. |

## Test plan

- **Unit (`tests/`)** in the new pack repo:
  - `test_deployments.py` — `register()` with monkeypatched `entry_points`.
  - `test_commands.py` — each aggregator with the underlying pack monkeypatched to (a) present, (b) absent → graceful degrade.
  - `test_pyproject.py` — read & assert curated deps + EP registrations.
  - `test_overlay.py` — overlay file exists at expected path; markdown body contains command references.
- **E2E (`tests/e2e/`)** in the pack repo:
  - `test_po_install_starter.py` — full `po install --editable .` then assert `po list`, `po packs`, `po doctor` show starter content. Uses an isolated `UV_TOOL_DIR=/tmp/...` to avoid clobbering the dev env.
  - `test_graceful_degrade.py` — monkeypatch `sys.modules` or use a separate uv tool env without `po-stripe`; assert `po spend` exits 0 with degrade message.
- **Playwright** — N/A (no UI per triage `has_ui: false`).
- **Core repo regression** — none expected; `prefect-orchestration` itself is untouched. The 6 baseline failures in `baseline.txt` are unrelated to this pack and must not regress (regression-gate's job, not ours).

## Risks

- **Aspirational dependencies.** `po-gmail`, `po-gcal`, `po-slack`, `po-attio`, `po-formulas-retro`, `po-stripe` either don't exist on PyPI yet or aren't even built. Listing them as hard deps will break `po install`. Mitigation: include only built+installable packs in `[project] dependencies`; record the rest as commented-out TODOs with bead refs (`# TODO 7vy: po-formulas-retro`) and document manual follow-up in README. Future bead can flip them to hard deps as packs land. **This means AC #1 is partially satisfied today** — call this out explicitly to critic; offer to ship the starter with a `[project.optional-dependencies] full = [...]` extras group as a workaround so `po install po-nanocorp-starter[full]` becomes the "install everything once it exists" UX without breaking today.
- **Repo location ambiguity.** Triage flags this. Going with sibling-dir convention; if the user wants it inside this rig instead, the move is `mv ../po-nanocorp-starter prefect-orchestration/packs/` + path tweaks. Not breaking.
- **Deployment registration import-time cost.** `register()` is called by core's `po deploy`. Iterating entry points is cheap, but if a formula's import side-effect is heavy we'd pay it at every `po deploy`. We **don't import the formula module** — we only enumerate entry-point names and synthesize deployment objects keyed by name. No import = no cost.
- **Welcome overlay path.** Choosing `.claude/welcome-starter.md` over `CLAUDE.md`. AC #4 says "welcome CLAUDE.md overlay" — interpretation: pack ships an overlay file that augments CLAUDE.md context. Document in README how to `@include` the welcome file in the rig's own CLAUDE.md if desired. Critic may want a literal `CLAUDE.md` in `overlay/` instead; the pack_overlay skip-existing logic makes that safe (won't clobber existing rig CLAUDE.md), so we can switch trivially.
- **Migrations / API contracts.** None — pure additive, new pack, no schema, no breaking change to core or to any consumer pack.
- **Test isolation.** E2E tests that shell out to `po install` mutate the global uv tool env. Use `UV_TOOL_DIR` and/or skip these tests in CI unless `PO_E2E_REAL_INSTALL=1` — same convention used in core's `tests/e2e/`.
