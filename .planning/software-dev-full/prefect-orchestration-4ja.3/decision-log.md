# Decision log — prefect-orchestration-4ja.3

- **Decision**: Used plain `mv` instead of `git mv` for the prompt → agent moves.
  **Why**: The pack lives at `/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/`, which is **not a git repo** (`.git` absent). `git mv` errored with `not a git repository`. Plain `mv` accomplishes the same content move; no history exists to preserve in the pack.
  **Alternatives considered**: Initialising a git repo in the pack just to use `git mv` — out of scope.

- **Decision**: Did not modify the pack's `pyproject.toml`.
  **Why**: The pack uses `hatchling` with `[tool.hatch.build.targets.wheel] packages = ["po_formulas"]`. Hatchling auto-includes all files inside the package directory when building a wheel, so `agents/<role>/prompt.md` ships automatically — no `package-data` glob change needed. The plan's mention of a pyproject change was conservative; verified unnecessary by reading the existing config.
  **Alternatives considered**: Adding an explicit `[tool.hatch.build.targets.wheel.force-include]` entry — would be redundant.

- **Decision**: Left `mail.md` at `po_formulas/prompts/mail.md` rather than moving to `po_formulas/mail.md` or `docs/`.
  **Why**: `mail.md` is a reusable prompt **fragment** (a comment in the file itself says "inline into builder/critic/verifier role prompts"), not loaded via `render()`. Moving it would risk breaking any human reference in the pack docs that points to its current path. The plan explicitly said either location was acceptable; staying put minimises churn.
  **Alternatives considered**: Move to `po_formulas/mail.md` next to `mail.py` — semantically equivalent, no behavioural impact.

- **Decision**: Did not create an `agents/reviewer/` folder despite the issue design listing both `build-critic` and `reviewer`.
  **Why**: There is only one critic prompt in the codebase that runs after each build iter (`render("review")` → conceptually the build-critic). No second "reviewer" prompt exists today. Per the plan's mapping table, `review.md` was renamed to `agents/build-critic/prompt.md`. If a future agent needs its own prompt, it gets a new dir then. This avoids creating an empty placeholder.
  **Alternatives considered**: Create `agents/reviewer/prompt.md` as a copy of `build-critic` — would duplicate without purpose and confuse the loader.

- **Decision**: `render_template()` raises `FileNotFoundError` (with role name) on missing prompt rather than `KeyError`.
  **Why**: Missing-file is fundamentally a filesystem condition; surfacing it as `FileNotFoundError` lets callers distinguish "you typo'd the role" from "you forgot a `{{var}}`". Earlier behaviour propagated the raw `OSError` from `read_text()` without context; new code re-raises with a clearer message that names the role and the resolved path.
  **Alternatives considered**: Swallow and return empty string — silent failure, would just push the failure deeper into the flow.

- **Decision**: Did not skip / xfail the pre-existing failing tests (`test_session_name_derivation`, `test_prompt_fragment_exists_and_mentions_inbox`).
  **Why**: The baseline at `baseline.txt` notes "baseline captured with existing failures"; these failures predate this issue and are unrelated to the agents/<role>/prompt.md migration. Out of scope.
  **Alternatives considered**: Fix the path in `test_prompt_fragment_exists_and_mentions_inbox` (it points at `prefect-orchestration/po_formulas/mail_prompt.md` which doesn't exist) — defer to whoever owns the mail-prompt module.

- **Decision**: Skipped `mcp-agent-mail file_reservation_paths`.
  **Why**: The agent identity `po-94fd4443` is not registered with the local Agent Mail server for this project (`Agent 'po-94fd4443' not found`). Falling back to scoped `git add <path>` discipline (per parallel-run hygiene point 1) — only commit files I touched.
  **Alternatives considered**: Register an agent identity and reserve — adds friction and the per-issue identity isn't expected to outlive this run.
