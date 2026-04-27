# Plan — prefect-orchestration-pw4

## Goal

Add a first-class **rig-path vs pack-path** split to `software-dev-full` so PO
self-dev (and any other "code-lives-elsewhere") issues can claim/close the
bead in the rig repo while landing actual code edits + commits in a separate
pack repo. Default behavior unchanged when neither override is supplied.

## Affected files

**Pack** (`/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/`):

- `po_formulas/software_dev.py` — accept `pack_path` kwarg on
  `software_dev_full`; resolve precedence (CLI > bd metadata > rig_path);
  thread `pack_path` into `base_ctx`; pass it to roles whose cwd should
  be the pack (builder/linter/ralph) via a new `RoleRegistry.code_path`
  field used as `repo_path` on those sessions.
- `po_formulas/agents/planner/prompt.md` — "Affected files" framed
  relative to `{{pack_path}}`; bd ops still in `{{rig_path}}`.
- `po_formulas/agents/builder/prompt.md` — `cd {{pack_path}}` before any
  edit / `git add` / `git commit`; capture diff with
  `git -C {{pack_path}} diff > {{run_dir}}/build-iter-{{iter}}.diff`;
  retain scoped `git add <path>` parallel-hygiene guidance.
- `po_formulas/agents/linter/prompt.md` — same `cd {{pack_path}}` before
  lint/test commands; lint log still under `{{run_dir}}`.
- `po_formulas/agents/ralph/prompt.md` — same.
- `po_formulas/agents/verifier/prompt.md` — "installed pack can import X"
  framed against the *installed* distribution (`uv pip show <dist>` /
  `python -c 'import <pkg>'` in the rig venv), not the source tree;
  reference both `{{pack_path}}` (where source landed) and `{{rig_path}}`
  (where venv lives).
- `po_formulas/agents/baseline/prompt.md`, `tester/prompt.md`,
  `regression-gate/prompt.md`, `deploy-smoke/prompt.md`,
  `triager/prompt.md` — disambiguate `{{rig_path}}` (bd / venv / smoke)
  vs `{{pack_path}}` (code under test). Where the issue is in-repo
  (default), the two are equal and prompts read identically.

**Core** (`/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/`):

- `prefect_orchestration/cli.py` — `po run` arg parser already
  forwards arbitrary `--key value`, so `--pack-path` flows through to
  the formula kwarg without changes; verify by inspection.
- `README.md` — new "Rig path vs pack path" section with a worked
  example for PO self-dev (rig=`prefect-orchestration`,
  pack=`software-dev/po-formulas`).
- `CLAUDE.md` (this repo) — short bullet under "polyrepo rigs"
  pointing at the new section.

**Tests** (pack):

- `tests/test_software_dev_pack_path.py` (new) — unit-level: dry-run
  `software_dev_full` with `pack_path` set, assert it appears in
  `base_ctx`, asserts builder/linter/ralph sessions get `repo_path`
  pointing at `pack_path` while triager/baseline/tester/verifier keep
  `repo_path = rig_path`.
- `tests/test_software_dev_pack_path_metadata.py` (new) — bd metadata
  `po.target_pack` resolution: precedence CLI > metadata > default.
  Stub `bd` lookup via `MetadataStore` / monkeypatch.

## Approach

1. **Signature** — extend `software_dev_full(...)` with
   `pack_path: str | None = None`. At flow entry, resolve effective
   pack-path:

   ```
   if pack_path is not None:        # CLI / Python explicit
       effective = Path(pack_path).expanduser().resolve()
   else:
       md = store.get("po.target_pack")  # bd metadata, set per-issue
       effective = Path(md).expanduser().resolve() if md else rig_path_p
   ```

   Validate: `effective.exists()` and `(effective / ".git").exists()`
   OR walk upward to find a `.git` ancestor (polyrepo case). If
   neither, log a yellow warning but continue (don't hard-fail —
   builder may create the dir).

2. **Context plumbing** — add `pack_path` to `base_ctx` (always set;
   equals `rig_path` in the default case so existing prompts without
   `{{pack_path}}` still render). All template renders pick it up
   automatically via `**ctx`.

3. **RoleRegistry** — add an optional `code_path: Path | None = None`
   field; when populated, it's used as `repo_path` for the
   code-editing role set: `{"builder", "linter", "ralph"}`. Other
   roles continue to receive `rig_path` — they `cd` into `rig_path`
   for bd / pytest / smoke commands. This keeps the AgentSession cwd
   correct for git ops without forcing prompts to wrap every command
   in `cd`.

   ```python
   def get(self, role: str) -> AgentSession:
       cwd = self.code_path if (self.code_path and role in _CODE_ROLES) \
             else self.rig_path
       ...
   ```

4. **Precedence** — CLI/python explicit `pack_path` wins; falls back
   to bead metadata `po.target_pack`; falls back to `rig_path`. Pin
   this in a helper `_resolve_pack_path(pack_path, store, rig_path_p)`
   so the rule is one place + unit-testable.

5. **Prompt updates** — non-default roles get `{{pack_path}}` in code
   ops; bd ops keep `{{rig_path}}`; artifact writes keep `{{run_dir}}`
   (which is `<rig_path>/.planning/...`). Where two distinct paths
   matter (e.g. builder's `git diff`), use `git -C {{pack_path}}` so
   the prompt is correct regardless of cwd.

6. **Bead metadata writeback** — also stamp `po.pack_path=<effective>`
   on the bead alongside the existing `po.rig_path` / `po.run_dir`
   stamps so `po logs` / `po artifacts` consumers can resolve where
   the code went.

7. **Docs** — README adds a "Polyrepo / pack-path" subsection with the
   self-dev example; CLAUDE.md links to it.

## Acceptance criteria (verbatim)

> (1) software_dev_full accepts an optional pack_path kwarg (default:
> equals rig_path).
> (2) Build/lint/ralph/verification prompts reference {{pack_path}} for
> code ops and {{rig_path}} for bead ops.
> (3) bd metadata 'po.target_pack' overrides the CLI default when
> present on the issue.
> (4) Smoke test: run a PO self-dev issue with
> rig_path=prefect-orchestration and
> pack_path=software-dev/po-formulas — code lands in the pack, bead
> updates in core.
> (5) README documents the split.

## Verification strategy

| AC  | How verified                                                                                                                                                  |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `inspect.signature(software_dev_full).parameters['pack_path'].default is None`; default-equality covered by unit test that asserts `ctx['pack_path'] == rig_path` when omitted. |
| 2   | `grep -l '{{pack_path}}' agents/{builder,linter,ralph,verifier}/prompt.md` returns four hits; planner/baseline/tester/regression-gate/triager/deploy-smoke retain `{{rig_path}}` references for bd/venv/smoke ops. |
| 3   | Unit test `test_software_dev_pack_path_metadata.py`: stub store with `po.target_pack=/tmp/A`, call resolver with `pack_path=None` → returns `/tmp/A`; with `pack_path=/tmp/B` → returns `/tmp/B` (CLI wins). |
| 4   | Manual smoke documented in README: `po run software-dev-full --issue-id <id> --rig prefect-orchestration --rig-path /…/prefect-orchestration --pack-path /…/software-dev/po-formulas --dry-run` exits 0; assert run_dir has `metadata.json` with `pack_path` set; live (non-dry) smoke optional given AC label "smoke", but plan documents the dry-run as the CI-runnable form. |
| 5   | `grep -A 5 'pack-path\|pack_path' README.md` shows the new section. |

## Test plan

- **Unit** (pack): two new tests under `software-dev/po-formulas/tests/`
  per "Affected files". Use `MetadataStore` via `FileStore` (no real
  bd needed). Backend = `StubBackend`.
- **E2E** (core, optional): not required — existing
  `tests/e2e/` exercise `po run` arg passthrough; adding a dry-run
  smoke that asserts `pack_path` is forwarded into `base_ctx` is
  cheap if time permits, but can be deferred as the unit test covers
  forwarding.
- **No Playwright** — CLI-only feature.

## Risks

- **Back-compat**: if `pack_path` defaults to anything other than
  `rig_path`, every existing pipeline silently retargets. Mitigation:
  default `None` → resolve to `rig_path`; explicit unit test for the
  default case.
- **Missing pack repo**: if `pack_path` doesn't exist or isn't a git
  repo, builder commits fail mid-flow. Mitigation: warn-and-continue
  at flow entry rather than hard-fail (see Approach §1) — non-git
  pack_path is a legitimate "create the dir" workflow.
- **AgentSession cwd churn** breaks per-role `--resume`: changing
  `repo_path` on a session is fine because session UUIDs are
  per-role and Claude resumes are not cwd-bound. Confirm by
  inspecting `AgentSession.session_id` lifecycle — UUID stored in
  bead metadata, cwd passed per-prompt.
- **No API contract change** in core CLI; `--pack-path` is a passthrough
  kwarg — no breakage for existing flows that don't pass it.
- **Polyrepo `git -C`**: prompts use `git -C {{pack_path}}` so the
  command works whether or not `pack_path` itself is the git root.
  If `pack_path` is a sub-tree of a larger repo, git walks upward
  automatically.
- **Verifier "installed pack can import X"**: ambiguity risk —
  reframed in the prompt to test the *installed distribution* in the
  rig's venv (since pack may not be `pip install -e`'d), not the
  source tree.
- **Scope creep**: epic-level inheritance (proposal 3) and
  worktree-per-run isolation are deferred — explicitly out of scope
  for this issue.
