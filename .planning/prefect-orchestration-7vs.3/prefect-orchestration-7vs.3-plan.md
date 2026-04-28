# Plan — prefect-orchestration-7vs.3

Migrate the **lint role** to the verdict-as-bead handoff model
(formulas-as-bead-graphs from epic 7vs). Smallest blast radius pilot —
only the lint role changes; all other roles stay on the verdict-file
contract.

## Files

1. **`prefect_orchestration/beads_meta.py`** — add helper:

   ```python
   def create_child_bead(
       parent_id: str,
       child_id: str,
       *,
       title: str,
       description: str,
       issue_type: str = "task",
       rig_path: Path | str | None = None,
       priority: int = 2,
   ) -> str:
       """Create a child bead with explicit id + parent edge.

       Idempotent: if `child_id` already exists, returns it without
       error. Returns the child id on success. NotImplementedError if
       `bd` is missing (FileStore has no graph support — the
       bead-mediated handoff requires bd).
       """
   ```

   Implementation: shells `bd create --id=<child_id> --parent=<parent_id>
   --title=… --description=… --type=task -p <priority>`. On `--id`
   collision (bd exit non-zero with "already exists" stderr) treat as
   success and return the id. Run with `cwd=rig_path`.

2. **`software-dev/po-formulas/po_formulas/software_dev.py::lint`** —
   replace body:

   ```python
   @task(name="lint", tags=["linter"])
   def lint(reg: RoleRegistry, ctx: dict[str, Any]) -> dict[str, Any]:
       parent_id = ctx["issue_id"]
       iter_n = ctx["iter"]
       child_id = f"{parent_id}.lint.{iter_n}"
       create_child_bead(
           parent_id,
           child_id,
           title=f"lint iter {iter_n} for {parent_id}",
           description=(
               f"Lint pass {iter_n} for {parent_id}. "
               f"Close with `--reason` containing 'clean' on success or "
               f"'failed' on failure (with `bd update --append-notes` "
               f"first carrying the failure summary)."
           ),
           rig_path=ctx["rig_path"],
       )
       sess = reg.get("linter")
       sess.prompt(render("linter", lint_bead_id=child_id, **ctx))
       reg.persist("linter")
       reg.publish(
           "lint",
           iter_n=iter_n,
           output_files=[f"lint-iter-{iter_n}.log"],
       )
       return _read_lint_verdict(child_id, ctx["rig_path"], iter_n)
   ```

   Plus a private helper:

   ```python
   def _read_lint_verdict(child_id: str, rig_path: str, iter_n: int) -> dict[str, Any]:
       """Build a verdict dict from the child lint bead's final state.

       - Closed with reason containing 'clean' → {"verdict": "pass", ...}
       - Closed with any other reason → {"verdict": "fail", "summary": notes_or_reason}
       - Still open → {"verdict": "fail", "summary": "agent crash: lint bead left open"}
       """
   ```

   Use `_bd_show(child_id, rig_path=rig_path)` from beads_meta to read
   `status`, `closure_reason` (or `reason`), and `notes`. Return shape
   matches the legacy verdict-file shape so downstream code that
   inspects `lint(...)` return remains compatible.

3. **`software-dev/po-formulas/po_formulas/agents/linter/prompt.md`** —
   rewrite the **Verdict file** section:

   ```markdown
   **Verdict (REQUIRED).** This iteration has its own bead `{{lint_bead_id}}`
   created by the orchestrator. Your final action MUST be one of:

   - **Pass:** `bd close {{lint_bead_id}} --reason "lint clean"`
   - **Fail:** First record failures:
     `bd update {{lint_bead_id}} --append-notes "<one-line failure summary>"`
     then close: `bd close {{lint_bead_id}} --reason "lint failed"`

   The orchestrator polls this bead's status; nothing else is parsed
   from your reply. **Do NOT write `verdicts/lint-iter-{{iter}}.json`** —
   that path is retired for the lint role.

   Reply with one line: `lint clean` or `lint failed: <reason>`.
   ```

   Drop the `verdicts/lint-iter-N.json` write block entirely.

4. **`software-dev/po-formulas/po_formulas/minimal_task.py`** —
   replace `read_verdict(...)` usage:

   ```python
   last_lint = lint(reg, build_ctx)  # returns verdict dict directly
   if last_lint.get("verdict") == "pass":
       lint_passed = True
       break
   ```

   Drop the `read_verdict(run_dir, f"lint-iter-{iter_}")` line and its
   import if unused elsewhere in the module.

5. **`software-dev/po-formulas/tests/test_software_dev_lint_bead.py`**
   — new file. Tests use a stub session and a `FakeBd` shim
   (subprocess monkeypatch on `subprocess.run`) so no real `bd` binary
   is required. Cases:

   - **clean-pass:** stub session calls `bd close --reason "lint
     clean"` mid-prompt; `lint(reg, ctx)` returns
     `{"verdict": "pass", ...}`.
   - **fail-then-fix:** iter 1 closes with `reason "lint failed"` +
     notes; assert verdict is `fail` with summary == notes. iter 2
     closes clean; assert pass.
   - **agent-crash:** stub session returns without touching bd; child
     bead remains open; assert verdict is `fail` with summary
     mentioning "agent crash" or "left open".

   Test the bd-shellout shape (correct `--id` / `--parent` / `--reason`
   args) via the monkeypatch, not by hitting a real bd database.

## Out of scope

- Other roles (build/critic/verifier/etc.) keep verdict-file contract.
- `software_dev_full` already doesn't gate on lint return — keep that
  behaviour. Just preserve return-shape compat.
- No changes to `watch()`. We use synchronous `sess.prompt()` + `bd
  show` after-the-fact; agent calls `bd close` *inside* its turn. The
  watch() primitive becomes load-bearing for fully-async roles in
  later 7vs children, not this one.

## Verification Strategy

| Criterion | Method | Concrete Check |
|---|---|---|
| (a) prompt updated with bd-close contract | grep | `grep -c 'bd close.*lint_bead_id' agents/linter/prompt.md` ≥ 1 AND `grep -c 'verdicts/lint-iter' agents/linter/prompt.md` == 0 |
| (b) verdict-file path removed for lint | code-read | `software_dev.py::lint` no longer writes `verdicts/lint-iter-*.json`; pack-wide grep for that path turns up 0 hits in non-test code |
| (c) end-to-end lifecycle | unit + manual | new tests assert `bd create` → agent closes → orchestrator reads notes; manual: a `po run minimal-task` on a small bead shows `bd ls` containing `<id>.lint.1` open → closed |
| (d) test coverage | pytest | `pytest tests/test_software_dev_lint_bead.py` shows 3 passing cases |

## Regression gate

Baseline: 703 passed, 10 failed (pre-existing — cli_packs, deployments, mail, agent_session_tmux), 2 skipped.

After change: same pass count or higher. The 10 pre-existing failures
are unrelated and remain.

## Decision points

- **Verdict shape on still-open bead:** treat as `fail` (not raise).
  Rationale: the iter loop in software_dev_full is permissive on lint
  failures (it doesn't gate); raising would change that behaviour and
  break the "smallest blast radius" framing.
- **Idempotent `create_child_bead`:** swallow "already exists" so
  retries (Prefect task retry, ralph re-entry) don't fail. The bead's
  prior state will simply be reused.
