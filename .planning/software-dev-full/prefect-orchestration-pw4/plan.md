# Plan — prefect-orchestration-pw4

## Goal

Add a first-class **rig-path vs pack-path** split to `software-dev-full`
so PO self-dev (and any other "code-lives-elsewhere") issues can
claim/close the bead in the rig repo while landing actual code edits
+ commits in a separate pack repo. Default behavior unchanged when
neither override is supplied.

## Audit — current state (per triage caveat: "may be partially shipped")

The triage flagged that this issue may already be shipped. An audit
of the live tree confirms most of the work has landed in core
(`prefect_orchestration/role_registry.py`) and pack
(`po_formulas/agents/*/prompt.md`, `po_formulas/software_dev.py`).
Per-AC status:

| AC | Status | Evidence |
| --- | --- | --- |
| 1. `software_dev_full` accepts optional `pack_path` kwarg (default = rig_path) | **shipped** | `software-dev/po-formulas/po_formulas/software_dev.py:371` — `pack_path: str \| None = None`; threaded into `build_registry` and `base_ctx` |
| 2. Build/lint/ralph/verification prompts reference `{{pack_path}}` for code ops, `{{rig_path}}` for bead ops | **shipped** | `grep -l '{{pack_path}}' agents/*/prompt.md` returns 8 prompts incl. builder, linter, ralph, verifier |
| 3. bd metadata `po.target_pack` overrides CLI default when present | **shipped (logic), gap (test)** | `prefect_orchestration/role_registry.py:148` — `_resolve_pack_path()` shells `bd show --json`, reads `metadata["po.target_pack"]`, applies CLI > metadata > rig_path precedence. No core unit test pins this contract — pack tests still import the symbol from its old pack location and fail at collection. |
| 4. Smoke: rig=prefect-orchestration, pack=software-dev/po-formulas — code in pack, bead in core | **demonstrated by previous session's commits** | Pack commit `d5ab8b3` and core commit `bda3382` came out of a single PO self-dev run with `--pack-path`; bead metadata still records `po.pack_path` per `bd show prefect-orchestration-pw4`. Adequate evidence; no need to re-run live |
| 5. README documents the split | **shipped** | `README.md:105` — "## Rig path vs pack path (cross-repo work)" with worked example + precedence list |

## Remaining work for this run

This run's `pack_path == rig_path == /home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration`,
so all edits land in the **core** repo. Two concrete deltas:

1. **Close the AC3 test gap** — add a unit test for
   `_resolve_pack_path` in core. The resolver moved from the pack
   into `prefect_orchestration/role_registry.py` during a refactor,
   leaving the pack-side tests
   (`software-dev/po-formulas/tests/test_software_dev_pack_path*.py`)
   importing symbols that no longer exist. Those pack tests are out
   of scope for this rig (this run's pack_path is core, not the
   formulas pack), but the resolver's new home in core deserves a
   regression-pinning test here so future refactors don't silently
   break the precedence contract.
2. **Verify AC5 wording is current** — `README.md:105+` covers the
   split. Spot-check that the example's CLI flags (`--pack-path`)
   match the actual kwarg name in `software_dev_full`'s signature
   (they do — `pack_path: str | None = None` ↔ `--pack-path`). No
   edit needed unless drift is found during the read.

## Affected files (this rig only)

**Core** (`/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration`):

- `tests/test_role_registry.py` (existing or new) — add 4 unit tests
  exercising `_resolve_pack_path` precedence: explicit > metadata,
  metadata > rig_path, no metadata → rig_path, `bd` missing → rig_path.
  Use `monkeypatch` on `prefect_orchestration.role_registry.shutil.which`
  and `subprocess.run` (same pattern the deleted pack tests used).
  No real `bd` invocation needed.
- `.planning/software-dev-full/prefect-orchestration-pw4/decision-log.md`
  — append the audit findings + the resolver-test-gap rationale.
- `.planning/software-dev-full/prefect-orchestration-pw4/lessons-learned.md`
  — note: when a helper migrates between repos, port its tests with
  it; orphaned tests fail at collection and gum up `regression-gate`.

**Out of scope for this run** (would require pack_path != rig_path):

- `software-dev/po-formulas/po_formulas/software_dev.py` — already
  shipped; no edit.
- `software-dev/po-formulas/po_formulas/agents/*/prompt.md` — already
  templated against `{{pack_path}}` / `{{rig_path}}`.
- `software-dev/po-formulas/tests/test_software_dev_pack_path*.py`
  — broken (imports `_CODE_ROLES` and `_resolve_pack_path` from
  `po_formulas.software_dev`, where they no longer live). Repairing
  these is a separate pack-side bead; this run cannot edit them
  without retargeting `--pack-path`. Document the situation in
  `lessons-learned.md` and either file a follow-up bead or note it
  for manual cleanup.

## Approach

1. **Read the existing core test layout** —
   `tests/test_role_registry.py` may already exist; if so, append
   the new precedence tests there. If not, create it. Match the
   imports + helper-style of nearby tests
   (`tests/test_*.py` per the `unit` layer convention in
   `CLAUDE.md` — top of `tests/`, no real subprocess).
2. **Write the resolver tests** following the precedence matrix:

   ```python
   from unittest.mock import patch
   from prefect_orchestration.role_registry import _resolve_pack_path

   def test_explicit_wins_over_metadata(tmp_path, monkeypatch):
       # bd would return po.target_pack=tmp_path/A, but caller passes B
       ...

   def test_metadata_used_when_no_explicit(...):  ...
   def test_falls_back_to_rig_when_no_metadata(...):  ...
   def test_falls_back_when_bd_missing(...):  ...
   ```

   Stub `shutil.which` to return `"bd"` or `None`; stub
   `subprocess.run` to return a `CompletedProcess` with a JSON
   payload like `[{"metadata": {"po.target_pack": "..."}}]`.
3. **Append decision log** — pin three decisions: (a) tests live in
   core because resolver lives in core; (b) keep `bd show --json`
   shell-out (no MetadataStore widening); (c) leave broken pack
   tests untouched this run since pack_path == rig_path.
4. **Run the unit suite** — `uv run python -m pytest tests/ -k role_registry`
   to confirm new tests pass without regressing siblings. The
   broader baseline failures (`test_agent_session_mail`,
   `test_cli_packs`, …) are pre-existing and unrelated to pw4 — do
   not chase them as part of this issue (regression-gate handles
   that triage).
5. **Commit** — single commit, `prefect-orchestration-pw4: pin
   _resolve_pack_path precedence in core test suite`. Scoped
   `git add tests/test_role_registry.py
   .planning/software-dev-full/prefect-orchestration-pw4/decision-log.md
   .planning/software-dev-full/prefect-orchestration-pw4/lessons-learned.md`.
   Do not `git add -A` — concurrent workers may have unstaged edits.

## Acceptance criteria (verbatim from the bead)

> (1) software_dev_full accepts an optional pack_path kwarg
> (default: equals rig_path).
> (2) Build/lint/ralph/verification prompts reference {{pack_path}}
> for code ops and {{rig_path}} for bead ops.
> (3) bd metadata 'po.target_pack' overrides the CLI default when
> present on the issue.
> (4) Smoke test: run a PO self-dev issue with
> rig_path=prefect-orchestration and pack_path=software-dev/po-formulas
> — code lands in the pack, bead updates in core.
> (5) README documents the split.

## Verification strategy

| AC | How verified in this run |
| --- | --- |
| 1 | `python -c "import inspect; from po_formulas.software_dev import software_dev_full; assert inspect.signature(software_dev_full).parameters['pack_path'].default is None"` — direct introspection of the live signature (already true; documented in audit table). |
| 2 | `grep -l '{{pack_path}}' /home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/po_formulas/agents/*/prompt.md` — must include `builder`, `linter`, `ralph`, `verifier`. (already true; documented in audit table.) |
| 3 | New unit tests in `tests/test_role_registry.py` exercise `_resolve_pack_path(explicit, issue_id, rig)` for all four precedence branches. Pinning this in core (resolver's home) closes the test gap caused by the pack→core refactor. |
| 4 | Historical evidence (commit pair `d5ab8b3` + `bda3382`) plus the bead's existing `po.pack_path` metadata stamp is sufficient — no live re-smoke required. If regression-gate insists on reproducing, the README's worked example is the canonical recipe. |
| 5 | `grep -A 5 'Rig path vs pack path' README.md` — section already exists at line 105. |

## Test plan

- **unit** (core, this rig): 4 new tests in
  `tests/test_role_registry.py` covering `_resolve_pack_path`
  precedence branches. Mock `shutil.which` and `subprocess.run`
  per the unit-layer rule (no real subprocess).
- **e2e**: not required. The pack_path passthrough is exercised
  every time anyone runs `software_dev_full`; behavior is observable
  in run-dir `metadata.json` (`po.pack_path` stamp) without a
  dedicated e2e. Per the rig `.po-env`, `PO_SKIP_E2E=1` is set so
  the run-tests step won't invoke them anyway.
- **playwright**: N/A — CLI/flow-only.

## Risks

- **Broken pack tests stay broken this run**. The pack-side
  `test_software_dev_pack_path*.py` files import symbols that
  migrated to core. Touching them requires `--pack-path` pointed at
  the pack repo, which this run is not. Surfacing the issue in
  `lessons-learned.md` is the most this rig can do; the pack's own
  next `software_dev_full` run will catch it via regression-gate.
- **Pre-existing baseline failures**. `baseline.txt` shows 25 failing
  tests (`test_agent_session_mail`, `test_cli_packs`,
  `test_deployments`, etc.) — none related to pw4. Resist scope
  creep. Document the pre-existing state in the decision log so
  regression-gate doesn't blame pw4's commit.
- **Resolver shell-out coupling**. `_resolve_pack_path` shells `bd
  show --json` directly rather than using the `MetadataStore`
  abstraction. The new tests bake this into contract; if a future
  refactor swaps to `MetadataStore`, the tests will need an update.
  Acceptable trade — the shell-out works without store wiring at
  flow entry (chicken-and-egg with `build_registry`).
- **Back-compat preserved**. `pack_path=None` continues to resolve
  to `rig_path`; no consumer of `software_dev_full` is forced to
  change.
- **No API contract change** — `--pack-path` is purely additive.
