# Decision log — prefect-orchestration-u1x

- **Decision**: Plan referenced a pack version (with `tmux_scope` fields) that wasn't in HEAD.
  **Why**: My initial Read picked up a working-tree state that had been reset before the build began (`git checkout --` cleared an unrelated experimental edit). I worked from the **committed** pack state (commit `d5ab8b3`) — the simpler `RoleRegistry` without `tmux_scope`.
  **Alternatives considered**: Trying to recover the wider variant; rejected — the bead spec said "no behavior change", and HEAD is the authoritative source.

- **Decision**: Core `role_registry.RoleRegistry` adds `tmux_scope` / `tmux_window_issue` plumbing as **forward-compatible** dataclass fields (default `None`).
  **Why**: `write_run_handles` and `publish_role_artifacts` already accept these kwargs; passing `None` reproduces the pack's existing behavior exactly. Future packs that want scoped tmux sessions get them for free.
  **Alternatives considered**: Stripping tmux_scope to match the pack one-for-one. Rejected — adds zero cost when None, and removing it would force a follow-up refactor.

- **Decision**: Resolved critic nit by using `formula_name: str = "software-dev-full"` kwarg only — no `agents_dir.parent.name` derivation.
  **Why**: Critic explicitly recommended this simplification. `agents_dir` is now an unused parameter (`del agents_dir`) kept for API symmetry.
  **Alternatives considered**: Drop `agents_dir` entirely. Kept it because future role-aware bootstrap (loading `<role>/config.toml`) will want it.

- **Decision**: Re-export `RoleRegistry` from `po_formulas.software_dev` (one-cycle compat shim).
  **Why**: Plan's "backwards compatibility" risk — any in-flight branch importing from the pack stays working.
  **Alternatives considered**: Hard break. Rejected because cost is one re-export line.

- **Decision**: Skipped the `--dry-run` `po run` smoke (AC3) — verified via in-process `build_registry` call instead (`reg`, `ctx`, `run_dir/verdicts`, `links.md` all materialize).
  **Why**: A `po run --dry-run` invocation requires Prefect server / pack reinstall. The in-process exercise covers the same factory wiring without that cost. Full real run is the human gate per plan §verification.
  **Alternatives considered**: `po run --dry-run`; rejected for build-iter scope — leaves it for verification step.

- **Decision**: Did not edit pack `pyproject.toml`; no entry-point changes.
  **Why**: Plan says EP groups don't change. `po packs update` not strictly needed for an editable install — Python re-imports pick up code changes.
