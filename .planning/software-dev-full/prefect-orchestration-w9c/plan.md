# Plan — prefect-orchestration-w9c

Path-aware `regression_gate`: only run test files reachable from the build's diff (plus an unconditional smoke set). `--full` flag preserves current full-suite behavior. `tests-changed.txt` artifact lands in the run_dir.

---

## Cross-repo note (read first)

**This is a self-dev / cross-repo issue.** The bead has
`po.pack_path == po.rig_path == /home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration`, but the formula being changed (`software_dev_full`) and the role prompt being updated (`agents/regression-gate/prompt.md`) live in the **sibling pack**:

```
/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/
```

Per `CLAUDE.md` ("Do land pack-contrib code in the pack's repo, not in the caller's rig-path") and the resolved beads issue `prefect-orchestration-pw4`, formula and prompt edits MUST land in that sibling repo and be committed there. The deterministic helper module is generic and lands in this rig (`prefect_orchestration/` core).

Builder: when the time comes, `cd` into the right repo before each `git add` / `git commit`, and use scoped `git add <path>` (other PO workers may be active in the rig concurrently).

The user instruction "list affected files with absolute paths under `/home/ryan-24/.../prefect-orchestration`" is followed where the file is actually in this rig; for files in the pack repo, the absolute pack-repo path is given and explicitly tagged `[PACK REPO — separate git commit]`.

---

## Affected files

**In this rig (`/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration`)**

- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/diff_mapper.py` — **new**. Deterministic Python helper: compute changed files vs a base ref, map source files to test files via stem heuristics, detect "tripwire" changes that force a full run, write/read the `tests-changed.txt` artifact. No Claude calls; pure stdlib + `subprocess`.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/__init__.py` — re-export the public helpers from `diff_mapper` if any other module needs them (likely none; the pack imports directly from the submodule).
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_diff_mapper.py` — **new** unit tests. Exercises diff computation against a temp-dir git repo, stem mapping, tripwire detection, write/read round-trip, and smoke-set merging.

**In the sibling pack repo `[PACK REPO — separate git commit]`**

- `/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/po_formulas/software_dev.py` — add a new `compute_diff_tests` `@task` between the lint/test fan-out and `regression_gate`; thread a `force_full_regression: bool = False` kwarg through `software_dev_full(...)` into `base_ctx`; ensure the value is plumbed into the regression-gate context.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/po_formulas/agents/regression-gate/prompt.md` — read `tests-changed.txt`, run only the listed tests + smoke set; fall back to the full suite when the artifact is missing or contains the `__FULL__` sentinel. Smoke-set list embedded inline.

(No edits to `baseline/prompt.md` for AC scope — baseline-opt-in is mentioned in the issue body but **not** in the four ACs; deferred to a follow-up bead per triage.)

---

## Approach

### 1. New module — `prefect_orchestration/diff_mapper.py`

Deterministic, no LLM. Public surface:

```python
TRIPWIRES: tuple[str, ...] = (
    "conftest.py",          # any path ending in this
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "bun.lockb",
    ".po-env",
    "pytest.ini",
    "setup.cfg",
)

DEFAULT_SMOKE_TESTS: tuple[str, ...] = (
    # Cheap, broad-import sweep tests this rig already has.
    "tests/test_doctor.py",
    "tests/test_packs.py",      # entry-point loading sanity
    "tests/test_role_registry.py",
)

def compute_changed_files(
    repo_path: Path,
    base_ref: str = "origin/main",
) -> list[Path]:
    """`git diff --name-only <merge-base>..HEAD` from inside repo_path.

    Uses `git merge-base <base_ref> HEAD` so multi-commit actor-critic
    branches see the full delta, not just HEAD~1..HEAD (triage risk #2).
    Falls back to `git diff --name-only HEAD~1..HEAD` if the ref is
    unknown (e.g. fresh clone, detached HEAD).
    """

def map_files_to_tests(
    changed: Iterable[Path],
    repo_root: Path,
    *,
    test_root: Path = Path("tests"),
) -> tuple[set[Path], bool]:
    """Map source file changes to test files via stem heuristics.

    Heuristics, in order:
      • change to `tests/**/test_*.py` → include that file directly.
      • change to `<pkg>/<sub>/<stem>.py` → look for
        `tests/<sub>/test_<stem>.py` and `tests/test_<stem>.py`.
      • change to a tripwire path (TRIPWIRES) → return (set(), True)
        immediately; caller must run the full suite.
      • unmatched non-test source change → no contribution. The smoke
        set is the safety net; the orchestrator merges it in.

    Returns (mapped_test_paths, force_full).
    """

def write_tests_changed(
    run_dir: Path,
    tests: set[Path],
    *,
    force_full: bool,
    smoke: Iterable[Path] = DEFAULT_SMOKE_TESTS,
) -> Path:
    """Write `tests-changed.txt` to run_dir. Returns the file path.

    Layout:
        # Generated by prefect_orchestration.diff_mapper @ <ISO ts>
        # base_ref=<ref>  changed_files=<n>  force_full=<bool>
        __FULL__                                  ← only when force_full
        tests/<...>.py                            ← otherwise: mapped ∪ smoke
        ...
    """

def read_tests_changed(
    run_dir: Path,
) -> tuple[list[str] | None, bool]:
    """Read tests-changed.txt. Returns (paths, force_full).

    (None, True)  → file missing OR `__FULL__` sentinel present.
    ([...], False) → list of test paths to run (already smoke-merged).
    """
```

The helper is the entire deterministic surface; the pack just calls `compute_changed_files` + `map_files_to_tests` + `write_tests_changed` and the role prompt only does `read`-equivalents in bash.

### 2. New flow task — `compute_diff_tests`

In `software_dev.py`, between the lint/test fan-out and `regression_gate`:

```python
@task(name="compute_diff_tests", tags=["tester"])
def compute_diff_tests(reg: RoleRegistry, ctx: dict[str, Any]) -> dict[str, Any]:
    from prefect_orchestration.diff_mapper import (
        compute_changed_files, map_files_to_tests, write_tests_changed,
    )
    if ctx.get("force_full_regression"):
        path = write_tests_changed(Path(ctx["run_dir"]), set(), force_full=True)
        return {"force_full": True, "artifact": str(path)}
    repo = Path(ctx["pack_path"])  # diff against the repo where build commits landed
    changed = compute_changed_files(repo)
    mapped, tripwire = map_files_to_tests(changed, repo)
    write_tests_changed(Path(ctx["run_dir"]), mapped, force_full=tripwire)
    return {"force_full": tripwire, "n_changed": len(changed), "n_mapped": len(mapped)}
```

This is an in-process Python `@task`, **not** a Claude turn (cheap, deterministic, observable in the Prefect UI). Wired in the flow body just before `regression_gate(reg, build_ctx)`:

```python
            ...
            for f in test_futs:
                f.wait()
            compute_diff_tests(reg, build_ctx)        # ← new
            reg_verdict = regression_gate(reg, build_ctx)
```

### 3. New flow kwarg — `force_full_regression`

Add to `software_dev_full(...)` signature:

```python
@flow(name="software_dev_full", flow_run_name="{issue_id}", log_prints=True)
def software_dev_full(
    issue_id: str,
    rig: str,
    rig_path: str,
    pack_path: str | None = None,
    iter_cap: int = 3,
    plan_iter_cap: int = 2,
    verify_iter_cap: int = 3,
    ralph_iter_cap: int = 3,
    parent_bead: str | None = None,
    dry_run: bool = False,
    claim: bool = True,
    force_full_regression: bool = False,    # ← new
) -> dict[str, Any]:
    ...
```

After `build_registry(...)` returns `base_ctx`, inject `force_full_regression` so every `build_ctx = {**base_ctx, "iter": iter_}` carries it:

```python
    base_ctx["force_full_regression"] = force_full_regression
```

The CLI already supports `--force-full-regression` / `--no-force-full-regression` via `_parse_kwargs` (handles `--key=value`, `--no-flag`, and bare `--flag`); no CLI changes required.

### 4. Update `regression-gate/prompt.md`

The current prompt unconditionally runs the full suite. Replace the test-execution stanza with:

```bash
cd {{rig_path}}
{
  echo "=== FINAL $(date -Iseconds) ==="
  ARTIFACT="{{run_dir}}/tests-changed.txt"
  if [ -f "$ARTIFACT" ] && ! grep -q '^__FULL__' "$ARTIFACT"; then
    # Path-aware: only listed tests + smoke (already merged by diff_mapper).
    TESTS=$(grep -v '^#' "$ARTIFACT" | grep -v '^$' | tr '\n' ' ')
    echo "running scoped suite: $TESTS"
    [ -f pyproject.toml ] && uv run python -m pytest $TESTS --tb=short 2>&1 | tail -50
  else
    # Missing artifact OR __FULL__ sentinel → full suite (current behavior).
    echo "running full suite (artifact missing or force-full)"
    [ -f pyproject.toml ] && uv run python -m pytest --tb=short 2>&1 | tail -30
  fi
  [ -f package.json ]   && bun test 2>&1 | tail -30 || true
  [ -f Makefile ]       && make test 2>&1 | tail -30 || true
} > {{run_dir}}/final-tests.txt 2>&1
```

The "compare against baseline; emit verdict" stanza is unchanged.

---

## Acceptance criteria (verbatim from issue)

1. After a build that only touches one module, regression_gate runs <5 min on prefect-orchestration.
2. tests-changed.txt artifact present in run-dir.
3. `--full` flag preserves current full-suite behavior.
4. Smoke set runs unconditionally so cross-cutting breakage still caught.

---

## Verification strategy

| AC | How verified |
|---|---|
| (1) <5 min one-module run | Indirect: unit test asserts that for a synthetic diff touching only `prefect_orchestration/diff_mapper.py`, `map_files_to_tests` yields exactly `{tests/test_diff_mapper.py}` (mapped) + smoke set (3 cheap files). Wall-clock verified manually with `time uv run python -m pytest tests/test_diff_mapper.py tests/test_doctor.py tests/test_packs.py tests/test_role_registry.py` inside the rig (expect <30 s). Documented in `lessons-learned.md` at end of run. |
| (2) `tests-changed.txt` artifact present | Unit test: `compute_diff_tests` task → assert `(run_dir / "tests-changed.txt").is_file()`. Round-trip test: written by `write_tests_changed`, parsed by `read_tests_changed`, returns same paths. |
| (3) `--full` preserves full-suite behavior | Unit test: with `force_full_regression=True` in ctx, the task writes `__FULL__` sentinel; `read_tests_changed` returns `(None, True)`. Bash-level sanity: prompt's `grep -q '^__FULL__'` matches the sentinel. Manual: `po run software-dev-full --issue-id <demo> --rig … --force-full-regression` — observe the regression_gate transcript runs `pytest --tb=short` (no positional test paths). |
| (4) Smoke set runs unconditionally | Unit test: `write_tests_changed(run_dir, tests=set(), force_full=False)` produces a file containing every `DEFAULT_SMOKE_TESTS` entry. Empty-diff case: `compute_diff_tests` against a clean working tree still emits the smoke set. |

Plus the floor obligation from `baseline-notes.md`: builder must keep ≤23 failing / ≥502 passing on the unit suite; any new red is a regression.

---

## Test plan

- **unit (this rig)** — primary signal. New file `tests/test_diff_mapper.py`. Cases:
  - `compute_changed_files` against a temp git repo (init, commit, branch off, modify a file, commit) returns the modified path.
  - Stem mapping: `prefect_orchestration/foo.py` → `tests/test_foo.py`; `prefect_orchestration/sub/bar.py` → `tests/sub/test_bar.py` and `tests/test_bar.py` (whichever exists).
  - Test file changes: `tests/test_x.py` → mapped to itself directly.
  - Tripwires: each of `conftest.py` / `pyproject.toml` / `uv.lock` / `.po-env` triggers `force_full=True`.
  - `write_tests_changed` + `read_tests_changed` round-trip including the `__FULL__` sentinel branch.
  - Smoke merging: empty mapped set still yields `DEFAULT_SMOKE_TESTS`.
- **e2e (this rig)** — `tests/e2e/` is gated by `PO_SKIP_E2E=1` in `.po-env` (CLAUDE.md), so the gate itself won't run e2e during the flow. Builder runs `uv run python -m pytest tests/e2e/` manually before declaring the iteration ready, only if e2e CLI surface area changed (it doesn't here).
- **playwright** — N/A (`has_ui=false`).

The new `compute_diff_tests` task in the **pack** is too thin to warrant its own pack-side test (3 lines of orchestration around the helper). The helper is fully unit-tested in core.

---

## Risks

- **Cross-repo split (PRIMARY).** Formula + role-prompt edits land in `/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/`, helper + tests land in this rig. Two `git commit`s, two repos. Builder must `cd` into the right tree before each commit and use scoped `git add <path>`. The bead's `po.pack_path` metadata is misconfigured (equals `po.rig_path`); flag in `decision-log.md` and consider a follow-up bead to fix the metadata. **Do not** attempt to colocate code by symlinking or moving the formula.
- **`HEAD~1..HEAD` is wrong inside the actor-critic loop.** Builder makes multiple commits per iteration (build → lint amend → ralph). Mitigation: `compute_changed_files` uses `git merge-base origin/main HEAD` and falls back to `HEAD~1..HEAD` only when the ref is missing. This rig has no remote (`CLAUDE.md`: "This repo has no git remote configured"), so the fallback path is the production path here — document the limitation; consider taking a snapshot SHA at flow start in a follow-up.
- **Hand-rolled stem mapping is brittle.** Reverse imports (changing `foo.py` may break a test that imports `foo` indirectly via `bar.py` ↔ `test_bar.py`) are not modeled. Mitigation: smoke set + tripwire fallback; `pytest --picked` / `pytest-testmon` is a follow-up per the issue design note.
- **Tripwire list completeness.** Missing a tripwire (e.g. `tox.ini`, `noxfile.py`) silently shrinks coverage. Mitigation: start with the conservative list above; iterate as misses surface. Document in `engdocs/`.
- **`force_full_regression` kwarg name collision.** `base_ctx` is `{**base_ctx, "iter": iter_}`-spread into every role prompt's render call; the new key must not shadow an existing template variable. Verified by `grep -rn "{{force_full" agents/` returning no hits.
- **No API contract break.** The new flow kwarg defaults to `False` — existing `po run software-dev-full …` invocations behave identically except that the gate now runs the scoped suite when the diff fits the heuristic. The runtime change for the average run is "regression_gate goes from 1h+ to <5 min" (the entire point), but the verdict file shape (`{"regression_detected": bool, ...}`) is unchanged, so no consumer downstream of regression_gate breaks.
- **Concurrent epics in one rig.** If two epics are running side-by-side and one writes a `tests-changed.txt` while another reads it, that would be a bug — but `run_dir` is per-issue (`<rig>/.planning/software-dev-full/<issue_id>/`), so the artifact is naturally isolated.
- **Empty `pack_path` git history.** If the pack repo is a fresh clone with no commits, `git diff` returns nothing; `compute_changed_files` returns `[]` and the gate runs only the smoke set. This is the correct behavior (no changes → no targeted tests) but worth a debug log line.
