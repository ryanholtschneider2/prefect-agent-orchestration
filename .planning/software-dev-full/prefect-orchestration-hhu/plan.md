# Plan — prefect-orchestration-hhu

Scoped per-iteration test selection + rig-local pass/fail cache + a final
`full_test_gate` task between verifier APPROVED and `bd close`. Conservative
defaults (full-suite fallback on tripwire / ambiguous mapping; never zero
tests) keep correctness; the end gate catches anything the scoped loop
misses.

---

## Cross-repo note (read first)

The bead's `po.pack_path` and `po.rig_path` both point at
`/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration`,
but per `CLAUDE.md` ("Do land pack-contrib code in the pack's repo, not
in the caller's rig-path") and the resolved `prefect-orchestration-pw4`
split, the **formula + role-prompt edits land in the sibling pack repo**:

```
/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/
```

Generic, pack-agnostic helpers (cache reader/writer, diff→scope helpers
beyond what `diff_mapper.py` already gives us) live in **core**
(`prefect-orchestration` rig). The builder must `cd` into the right git
ancestor before each `git add` / `git commit` and use scoped
`git add <path>` (other PO workers may be active concurrently — see
ralph-prompt / build-prompt guidance).

Affected-file paths below are absolute. Files in the pack repo are
tagged `[PACK REPO — separate git commit]`.

There is also a sibling in-flight bead `prefect-orchestration-w9c`
(path-aware `regression_gate`) that ships the *gate* version of the
same idea. The diff-mapper helper module already exists in core
(`prefect_orchestration/diff_mapper.py`); w9c's pack-side prompt
update is independent — `hhu` does **not** modify
`agents/regression-gate/prompt.md` and does not block on w9c.

---

## Affected files

### In this rig — `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration`

- `prefect_orchestration/test_cache.py` — **new**. Rig-local pass/fail
  cache at `<rig>/.po-cache/tests.json`. Atomic JSON read-modify-write
  (`os.replace` + `fcntl.flock`), key
  `sha256(f"{layer}|{source_hash}|{collection_hash}|{scope_hash}")`.
  Public surface: `cache_key(...)`, `cache_get(rig, key)`,
  `cache_put(rig, key, verdict, *, run_id)`,
  `compute_source_hash(rig, paths=("prefect_orchestration", "tests"))`,
  `compute_collection_hash(rig, layer, scope)`,
  `compute_scope_hash(scope)`. Pure stdlib.
- `prefect_orchestration/diff_mapper.py` — **edit**. Add a
  `layer`-aware variant: `map_files_to_tests(..., *, layer="unit")`
  prunes mapped paths to the layer's prefix (`tests/e2e/...` for `e2e`,
  top-of-`tests/` for `unit`, etc.) so per-iter scoping never crosses
  layers. Also widen TRIPWIRES with `requirements.txt`, `Makefile`,
  `*.cfg` patterns (cheap; matches triage open question
  "needs full suite heuristics"). Public surface unchanged otherwise;
  existing callers (none yet land — w9c hasn't shipped its prompt
  edit) keep working.
- `prefect_orchestration/__init__.py` — **edit**. Re-export
  `test_cache` public surface alongside `diff_mapper` for parity.
- `tests/test_test_cache.py` — **new** unit tests. Round-trip
  (`cache_put` → `cache_get`); concurrent writes don't corrupt the
  JSON (spawn N threads, all `cache_put`, file remains valid JSON
  with all entries); atomic-replace semantics (mid-write crash leaves
  prior file intact); source-hash changes invalidate; collection-hash
  changes invalidate; scope-hash changes invalidate; missing
  `.po-cache/` is created lazily.
- `tests/test_diff_mapper.py` — **edit**. Add layer-aware mapping
  cases (changed `prefect_orchestration/foo.py` with `layer="unit"`
  yields `tests/test_foo.py` only, never `tests/e2e/...`; changed
  `tests/e2e/test_x.py` with `layer="unit"` yields `set()`).
- `.planning/software-dev-full/prefect-orchestration-hhu/decision-log.md` —
  **new** during build. Captures path-mapping vs `pytest-testmon`
  tradeoff and the swap path if scoping proves lossy in practice.
  Verbatim AC (6).

### In the sibling pack repo `[PACK REPO — separate git commit]`

- `/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/po_formulas/software_dev.py` —
  **edit**. Add `_build_test_cmd(rig_path, layer, scope, *, full)`
  helper (referenced as already-existing in `CLAUDE.md` but not
  actually present yet — this lands it). Thread `test_cmd` (and
  `scope_summary`) into the tester `ctx`. Add cache lookup around the
  agent turn: hit ⇒ skip the Claude call, write verdict directly. Add
  `full_test_gate` `@task` and a post-ralph loop that calls it,
  routing to ralph with `gate_failures` ctx on failure (capped by a
  new `gate_iter_cap: int = 2` flow kwarg, separate from
  `ralph_iter_cap`).
- `/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/po_formulas/agents/tester/prompt.md` —
  **edit**. The `{{test_cmd}}` placeholder is already in the prompt
  but no caller populates it (rendered today via accidental coverage
  in baseline). Replace with explicit guidance: "Run *exactly* the
  command in `{{test_cmd}}` — the orchestrator scoped it to your
  iteration's diff. Do NOT widen scope. The full suite runs after
  verifier approval as a final gate." Keep the verdict-write block.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/po_formulas/agents/full-test-gate/prompt.md` —
  **new**. Single role prompt: run the full pytest suite per enabled
  layer (the orchestrator computes the command per `_build_test_cmd(..., full=True)`),
  emit `verdicts/full-test-gate.json` with `{"passed": bool, "failures": [...], "summary": "..."}`.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/po_formulas/agents/ralph/prompt.md` —
  **edit**. When `{{gate_failures}}` is non-empty, treat the failures
  as the priority — fix them first; do not consider this a "no
  improvement" turn. (Optional ctx var; renders empty when absent so
  existing iter-3-after-verification path is unchanged.)
- `/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/tests/test_software_dev_test_cmd.py` —
  **new** unit test. Exercises `_build_test_cmd` for each layer
  with/without scope/full; asserts `--ignore` is set for sibling
  layer dirs, scope paths are quoted, full=True ignores scope.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/tests/test_full_test_gate.py` —
  **new** unit test. With a stub backend / mocked cache, asserts:
  gate runs after verification, gate-pass closes the bead, gate-fail
  injects `gate_failures` into ralph ctx, gate cap is honored.

---

## Approach

### 1. Cache module — `prefect_orchestration/test_cache.py`

```python
# pseudo-signature
def compute_source_hash(rig: Path, paths: Sequence[str]) -> str:
    """sha256 over `git ls-files` content of given paths.

    Uses `git ls-files` so untracked junk (__pycache__/, .pyc) does NOT
    poison the key — directly addresses triage open question
    "Git state vs source-tree hash".
    """

def compute_collection_hash(rig: Path, layer: str, scope: list[Path] | None) -> str:
    """`pytest --collect-only -q --no-header <args>` → sha256 of stdout.

    Captures parametrize / fixture / collection changes that source-hash
    misses. Cheap (~100ms) compared to running tests.
    """

def compute_scope_hash(scope: list[Path] | None) -> str:
    """sha256 of sorted-newline-joined scope; well-defined for `None`
    (==full-suite) so full-suite cache entries collide cleanly across
    iters."""

def cache_key(layer: str, source_hash: str, collection_hash: str, scope_hash: str) -> str:
    return sha256(f"{layer}|{source_hash}|{collection_hash}|{scope_hash}".encode()).hexdigest()

def cache_get(rig: Path, key: str) -> dict[str, Any] | None: ...
def cache_put(rig: Path, key: str, verdict: dict[str, Any], *, run_id: str) -> None:
    """Atomic: tmpfile in same dir → fsync → os.replace → fsync(dir).
    Holds an `fcntl.flock` for the whole RMW so concurrent epic flows
    don't lose entries. Records `produced_by`, `produced_at` for audit
    (triage open question "Cache key correctness under concurrent epics").
    """
```

Schema (`<rig>/.po-cache/tests.json`):

```json
{
  "version": 1,
  "entries": {
    "<sha256>": {
      "layer": "unit",
      "passed": true,
      "count": 502,
      "summary": "...",
      "scope_paths": ["tests/test_foo.py"],
      "source_hash": "...",
      "collection_hash": "...",
      "scope_hash": "...",
      "produced_at": "2026-04-27T20:15:00+00:00",
      "produced_by": "<flow_run_id>"
    }
  }
}
```

Stale-entry GC is out of scope; entries don't grow unbounded in
practice (each unique tree state is a single entry). A future bead
can add a TTL pruner if the file balloons.

### 2. Per-iteration scoping inside `run_tests` (pack-side)

`software_dev._build_test_cmd(rig_path, layer, scope, *, full)`:

- Resolves the layer's test directory (top of `tests/` for `unit`,
  `tests/e2e/` for `e2e`, `tests/playwright/` for `playwright`).
- Emits `--ignore=tests/<sibling>` for every sibling layer dir that
  exists on disk so layers stay non-overlapping (matches existing
  CLAUDE.md guidance).
- When `full=True` or `scope is None`: runs the whole layer dir
  (full-suite fallback path).
- When `scope` is a non-empty list of test files: passes them
  positionally after the layer dir, with the same `--ignore`s.
- Always prepends `cd {rig_path}` and pipes through
  `tee {run_dir}/{layer}-iter-{iter}.log`.

`run_tests` becomes:

```python
@task(name="run_tests", tags=["tester"], timeout_seconds=900)
def run_tests(reg, ctx, layer):
    rig_path = Path(ctx["rig_path"])
    pack_path = Path(ctx["pack_path"])
    run_dir = Path(ctx["run_dir"])
    iter_ = ctx["iter"]
    # 1) scope from build diff (pack-side git, since that's where edits land)
    changed = compute_changed_files(pack_path, base_ref="origin/main")
    scope, force_full = map_files_to_tests(changed, pack_path, layer=layer)
    scope_list: list[Path] | None = None if force_full else sorted(scope)
    if scope_list == []:
        # AC: never run zero tests for a layer — full fallback.
        scope_list = None

    # 2) cache lookup
    src_h = compute_source_hash(rig_path, ("prefect_orchestration", "tests"))
    col_h = compute_collection_hash(rig_path, layer, scope_list)
    scp_h = compute_scope_hash(scope_list)
    key = cache_key(layer, src_h, col_h, scp_h)
    cached = cache_get(rig_path, key)
    if cached is not None:
        get_run_logger().info(f"test cache hit {layer} ({key[:12]}…) — skipping turn")
        # Write verdict file directly so downstream tasks read it like normal.
        verdicts_dir = run_dir / "verdicts"
        verdicts_dir.mkdir(parents=True, exist_ok=True)
        (verdicts_dir / f"{layer}-iter-{iter_}.json").write_text(json.dumps({**cached, "cached": True}))
        reg.publish(f"tester-{layer}", iter_n=iter_, output_files=[f"{layer}-iter-{iter_}.log"])
        return cached

    # 3) cache miss → render the prompt with the scoped test_cmd, run agent
    test_cmd = _build_test_cmd(rig_path, layer, scope_list, full=False)
    sess = reg.get("tester")
    verdict = prompt_for_verdict(
        sess,
        render("tester", layer=layer, test_cmd=test_cmd,
               scope_summary=_summarize_scope(scope_list, force_full), **ctx),
        run_dir, f"{layer}-iter-{iter_}", fork=True,
    )
    reg.publish(f"tester-{layer}", iter_n=iter_, output_files=[f"{layer}-iter-{iter_}.log"])
    cache_put(rig_path, key, verdict, run_id=os.environ.get("PREFECT_FLOW_RUN_ID", "local"))
    return verdict
```

Why source-tree-hash hashes paths *in the rig*: the agent runs
`pytest` against `rig_path`'s venv (per the existing tester prompt:
`cd {{rig_path}}` before pytest). Tests live in the rig. Hash the
rig's source/test trees; the pack-vs-rig split is a separate dimension
that doesn't affect what tests cover.

### 3. `full_test_gate` task (pack-side)

```python
@task(name="full_test_gate", tags=["tester"])
def full_test_gate(reg, ctx) -> dict[str, Any]:
    rig_path = Path(ctx["rig_path"])
    layers: list[str] = []
    if os.environ.get("PO_SKIP_UNIT") != "1":
        layers.append("unit")
    if os.environ.get("PO_SKIP_E2E") != "1":
        layers.append("e2e")
    if ctx.get("has_ui") and os.environ.get("PO_SKIP_PLAYWRIGHT") != "1":
        layers.append("playwright")
    test_cmds = "\n\n".join(
        f"# {l}\n" + _build_test_cmd(rig_path, l, scope=None, full=True)
        for l in layers
    )
    sess = reg.get("tester")
    verdict = prompt_for_verdict(
        sess,
        render("full-test-gate", layers=", ".join(layers), test_cmds=test_cmds, **ctx),
        Path(ctx["run_dir"]), "full-test-gate",
    )
    reg.persist("tester")
    reg.publish("full-test-gate", iter_n=1, output_files=["full-test-gate.log"])
    return verdict
```

Flow integration (replaces the existing post-verify ralph loop):

```python
# 13. RALPH (existing, runs to completion or cap)
ralph_iter = 1
while ralph_iter <= ralph_iter_cap:
    rv = ralph(reg, {**base_ctx, "ralph_iter": ralph_iter, "gate_failures": []})
    if not rv.get("ralph_found_improvement"):
        break
    ralph_iter += 1

# 13.5. FULL TEST GATE — final safety net since iter loop ran scoped
gate_iter = 1
while gate_iter <= gate_iter_cap:
    gate = full_test_gate(reg, {**base_ctx, "has_ui": has_ui})
    if gate.get("passed"):
        break
    if gate_iter >= gate_iter_cap:
        logger.warning(f"gate_iter_cap={gate_iter_cap} hit — gate failing on close")
        break
    # Inject failing tests into a ralph turn dedicated to gate-fix
    rv = ralph(reg, {**base_ctx, "ralph_iter": ralph_iter,
                     "gate_failures": gate.get("failures", [])})
    ralph_iter += 1
    gate_iter += 1
store.set("gate_iter_final", str(gate_iter))
```

Separate `gate_iter_cap` (default 2) keeps gate-fix budget isolated
from "did ralph find clean-up improvements?" budget — addresses
triage open question "`full_test_gate` failure budget".

A failed gate at cap **does not** block `bd close` (matches existing
behavior of verifier/ralph caps — flow proceeds and logs a warning;
the failing verdict file is the audit trail). If the user wants strict
gate-on-close, that's a follow-up bead.

### 4. Tester prompt update (pack-side)

Today's prompt has `{{test_cmd}}` but no caller populates it — the
prompt rendered to date relies on `KeyError` being avoided by some
callers having `test_cmd` already in ctx (it doesn't). Strictly
speaking the prompt currently raises in `render_template`; this issue
fixes that by always populating `test_cmd` when `run_tests` is the
caller.

The new prompt body keeps the verdict-write block verbatim but adds:

> Run **exactly** the command in `{{test_cmd}}` — the orchestrator
> scoped it to this iteration's diff so the actor-critic loop is fast.
> Do NOT widen the scope yourself; the **full** suite runs after
> verifier approval as a `full_test_gate`. If `{{scope_summary}}` says
> "full layer (tripwire detected)", the command IS the full layer —
> proceed normally.

### 5. Decision log (rig-side, AC 6)

`<run_dir>/decision-log.md` records:

- **Path-mapping vs testmon**: testmon's SQLite cache is fragile
  across `po retry` (run-dir archived) and parallel epics in the same
  rig (no per-flow isolation); coverage tracing adds overhead that
  defeats some of the wall-clock win; agent-driven scoping plus
  conservative TRIPWIRE fallback is good enough for this codebase's
  flat layout, and the `full_test_gate` end-stop guarantees
  correctness.
- **Swap path if scoping proves lossy**: replace `_build_test_cmd`'s
  scope source with `pytest --picked` or testmon-driven selection
  while keeping the same `(test_cmd, scope_list)` interface and the
  same end-gate. No flow-graph change required for the swap.

---

## Acceptance criteria (verbatim from the issue)

- [ ] Test-runner agent prompt updated to emit scoped pytest selectors
      based on iteration diff
- [ ] `.po-cache/tests.json` cache reads/writes with
      `(layer, tree-hash, collection-hash)` key
- [ ] `full_test_gate` task runs after verifier approval, before bd close
- [ ] Failed full gate routes to ralph with failing-test context (does not close)
- [ ] Wall-clock for an N-iter run with 1-file-touch budget reduced by
      ~40% on `prefect-orchestration` rig (measured)
- [ ] Decision log captures path-mapping vs testmon tradeoff and the
      swap path if needed

---

## Verification strategy

| AC | How verified |
|---|---|
| 1. scoped selectors in tester prompt | Read `agents/tester/prompt.md` — `{{test_cmd}}` populated, "Do NOT widen scope" guidance present, sample render asserted in unit test (`test_software_dev_test_cmd.py`). |
| 2. `.po-cache/tests.json` RMW with composite key | `tests/test_test_cache.py`: round-trip with synthetic verdict; verifies key is `sha256(layer|src|col|scope)`; verifies file lives at `<rig>/.po-cache/tests.json`; concurrent-write test (10 threads) leaves valid JSON with all entries. |
| 3. `full_test_gate` between verifier and close | Pack-side `tests/test_full_test_gate.py` with a stub backend: drive `software_dev_full` to verifier-APPROVED, assert `full_test_gate` task ran (Prefect task graph inspection or call-counter in the stub) and assert it ran *after* verification, *before* `close_issue`. |
| 4. failed gate routes to ralph w/ failing tests | Same stub: gate verdict `{"passed": false, "failures": ["test_x", "test_y"]}` triggers a ralph call whose ctx has `gate_failures == ["test_x", "test_y"]`. Cap honored: 3rd consecutive gate failure proceeds without close. |
| 5. ~40% wall-clock reduction | Measured benchmark: pick a small reproducer bead (e.g. a 1-line core edit), run `software_dev_full` against `prefect-orchestration` rig three times before (current main) and three times after (this branch). Baseline measurement with `PO_SKIP_E2E=1` already set (matches normal operation). Median wall-clock for the `run_tests` Prefect task across iters compared. Capture in `verification-report-iter-N.md` with raw `prefect flow-run inspect` durations. Branch must hit ≥40% reduction. |
| 6. decision log entry | `decision-log.md` contains a "Path mapping vs testmon" section with the tradeoff table and swap-path instructions. Verifier reads it; failure if missing. |

---

## Test plan

| Layer | What runs | Lives in |
|---|---|---|
| **unit** (rig) | `test_test_cache.py`, `test_diff_mapper.py` (extended) | `tests/` (top of) |
| **unit** (pack) | `test_software_dev_test_cmd.py`, `test_full_test_gate.py` | `software-dev/po-formulas/tests/` |
| **e2e** (rig) | None new — existing `tests/e2e/` is gated by `PO_SKIP_E2E=1` in `.po-env`; no new subprocess-driven coverage required for hhu. | `tests/e2e/` |
| **playwright** | n/a — no UI work | n/a |

Runtime budget per layer:

- Rig unit additions: <2 s (cache module is pure stdlib; concurrent
  test uses 10 threads × 1 cache write ~50 ms total).
- Pack unit additions: <1 s (stub backend, no real Claude or pytest
  shell-out).
- No e2e cost added.

The pack-side flow tests **must** mock the agent backend
(`StubBackend`) so they don't shell out to real `pytest` for the
`full_test_gate` task — they drive the verdict file directly via the
stub.

Manual verification once code lands: run a small `po run software-dev-full`
against this rig with a 1-file edit and observe (a) `tester-iter-N.log`
runs only the targeted file, (b) `.po-cache/tests.json` populated,
(c) second iter on same source state hits the cache (log line
"test cache hit"), (d) `full-test-gate.log` runs the full unit suite.

---

## Risks

- **Pack-vs-rig confusion.** Bead points at the rig but most edits
  land in `po-formulas`. Builder must commit each edit in its own git
  ancestor, scoped `git add <path>`, and run `po packs update` after
  the pack edit so entry-point metadata is current before any
  end-to-end verification (`software_dev_full` is loaded via entry
  point, not by path).
- **`{{test_cmd}}` placeholder is currently unfed.** The current
  tester prompt references `{{test_cmd}}` but `run_tests` doesn't
  populate it. Render today raises `KeyError`. Build must populate it
  *before* shipping — prompt + flow change must land in the same
  commit (or the prompt must be updated last). Pack tests should
  catch this.
- **Cross-cutting test misses.** Pure basename matching misses
  cross-cutting tests (e.g. `test_e2e_graph_cli.py` covering changes
  in `graph.py`). Mitigated by:
  - The TRIPWIRE list (`conftest.py`, `pyproject.toml`, …) forces
    full when invalidating files change.
  - Test files touched in the diff are included directly via
    `_is_test_file`.
  - The `full_test_gate` end-stop catches anything missed.
  - Failure mode is "ralph budget consumed defending against an issue
    the iter loop never saw" → mitigated by separate `gate_iter_cap`.
- **Cache poisoning under concurrent epics.** Mitigated via
  `fcntl.flock` + atomic `os.replace` + `produced_by` audit field.
  Same `(layer, src, col, scope)` from two flows produces the same
  result anyway; collision is benign.
- **`po retry` and the cache.** Per triage: retry doesn't change
  source so the cache hit is correct. Documented in the decision
  log. No code change needed; verified with a manual `po retry`
  pass during build verification.
- **Source-tree-hash includes untracked junk.** Mitigated by hashing
  via `git ls-files` (tracked files only) — directly addresses triage
  open question. `__pycache__/`, `.pyc`, untracked `.tmp` files don't
  affect the key.
- **`collection_hash` requires a Python with the project venv
  available.** Cache lookup is best-effort: if `pytest --collect-only`
  exits non-zero, the helper returns a sentinel `"COLLECTION_FAILED"`
  which forces a cache miss (test runs, agent collects normally).
  Non-fatal; the orchestrator never raises on collection failure.
- **No backwards-compat break.** `software_dev_full(...)` adds a new
  kwarg `gate_iter_cap: int = 2` with a default, so all existing
  callers (CLI, `epic` formula via `graph_run`, scheduled deployments)
  keep working without changes. Prompt placeholder addition is a
  superset; old prompt would still render (every variable referenced
  is now provided).
- **Migrations.** None. Cache file is plain JSON, created lazily;
  schema field `version: 1` lets a future migration recognize stale
  caches and drop them.
- **API contract.** Verdict file format extended with optional
  `cached: true` marker. Existing readers (regression-gate prompt,
  `parsing.read_verdict`) tolerate extra fields — no consumer breaks.
