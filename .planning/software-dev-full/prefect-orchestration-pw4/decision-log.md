# Decision log — prefect-orchestration-pw4 (build iter 1)

- **Decision**: Lookup `po.target_pack` via direct `bd show <issue_id> --json`
  inside `_resolve_pack_path()` rather than reusing `MetadataStore.get`.
  **Why**: `auto_store(parent_bead, run_dir)` is keyed on the *parent* epic
  (or a `FileStore` fallback). The resolution rule in the plan reads the
  *issue's own* metadata; routing it through the parent store would either
  miss issue-level overrides or require widening `MetadataStore` semantics
  beyond what the rest of the flow uses. A small, single-purpose
  `bd show <id>` shell-out is cheaper than that refactor.
  **Alternatives considered**: pass `issue_id` to a new `MetadataStore.get_for_issue()`
  method (rejected: blurs the parent-vs-issue distinction); build a second
  `BeadsStore` keyed on `issue_id` (rejected: fights the dataclass).

- **Decision**: Add `code_path: Path | None` to `RoleRegistry` and a
  `_CODE_ROLES = {"builder", "linter", "cleaner"}` set, instead of
  threading `pack_path` everywhere or extending `AgentSession`.
  **Why**: keeps the routing rule local — one `_cwd_for_role()` method —
  while leaving non-code roles (triager/tester/verifier/critic/...) on
  `rig_path` for bd / venv / smoke ops. Matches the plan's rule that
  artifact writes stay under `rig_path`.
  **Alternatives considered**: per-role overrides in a dict (rejected as
  premature flexibility — three role names is fine as a literal); always
  use pack_path (rejected: breaks tester/verifier/regression-gate which
  must run inside the rig venv).

- **Decision**: When `pack_path == rig_path`, set
  `RoleRegistry.code_path=None` rather than `code_path=rig_path`.
  **Why**: makes the "default / no split" case identical to the pre-pw4
  code path — `_cwd_for_role()` short-circuits on `code_path is None` and
  returns `rig_path` for every role, exactly as before. Reduces blast
  radius for existing pipelines.

- **Decision**: Soft-warn (don't hard-fail) when `pack_path` doesn't exist
  or isn't inside a git repo.
  **Why**: per the plan's "Missing pack repo" risk — non-existent
  `pack_path` is a legitimate "create the dir" workflow; non-git
  `pack_path` could be a sub-tree of a parent repo (we walk up to find a
  `.git` ancestor). Failing at flow entry would block every legitimate
  cross-repo run.

- **Decision**: Use `git -C {{pack_path}}` in the builder's diff capture
  rather than relying on `cd` having been run.
  **Why**: AgentSession's cwd is set to `pack_path` for builder via the
  registry, but the prompt is written to be correct even if a future
  refactor changes that. Belt-and-suspenders for a one-character cost.
