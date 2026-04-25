# Decision log — prefect-orchestration-7vy

- **Decision**: Pack lives at `../po-formulas-retro/` as a sibling of
  `software-dev/po-formulas/`, NOT inside the `prefect-orchestration` rig.
  **Why**: CLAUDE.md `pw4` ("land pack-contrib code in the pack's repo, not in the caller's rig-path") and triage flag.
  **Alternatives considered**: nesting under `prefect-orchestration/` — rejected per the polyrepo rule.

- **Decision**: Verdict file shape is `{recurring: [...], single_occurrence: [...]}` written by the synthesizer, validated and *re-filtered* orchestrator-side via `analysis.filter_recurring`.
  **Why**: Plan §4–§5 requires a 3-distinct-runs bar plus an allow-list on `target_file`. The orchestrator can't trust the agent to enforce either, so it re-checks both before committing.
  **Alternatives considered**: parsing a unified diff string. Rejected — opaque to validate, and direct file writes are simpler given the narrow allow-list.

- **Decision**: `edit` accepts either a full-file string OR `{"append": str}`.
  **Why**: append is the common case (CLAUDE.md / prompt rubric additions); full-rewrite supports occasional larger reshaping. Both go through `_apply_edit`, which is straightforward to test.
  **Alternatives considered**: append-only — too restrictive; full-rewrite-only — too risky for prompts that already work.

- **Decision**: Bypass commit hooks via `git -c core.hooksPath=/dev/null commit`.
  **Why**: Plan risk — a target repo with a pre-commit hook that auto-pushes (husky-style) could exfiltrate the retro branch before a human reviews it.
  **Alternatives considered**: trust the target repo. Rejected — defense-in-depth on the no-push policy is cheap.

- **Decision**: `bd remember` shell-out is captured behind module-level `_bd_remember(text) -> bool` that tests monkeypatch.
  **Why**: Subprocess calls are awkward to mock with monkeypatch.setattr on `subprocess.run`; one named seam keeps tests clean.
  **Alternatives considered**: patching `subprocess.run`. Rejected — touchier and less explicit.

- **Decision**: Branch-name collision resolution appends `-1`, `-2`, ... to `retro/<utc-ts>` rather than incrementing the timestamp.
  **Why**: Same UTC second is the only realistic collision; preserving the timestamp keeps the branch name greppable.
  **Alternatives considered**: bumping the seconds field — confusing if logs reference the original timestamp.

- **Decision**: `_select_backend` mirrors software-dev's `PO_BACKEND` switch (stub | tmux | cli, default tmux-if-available).
  **Why**: Consistency with the existing pack lets `PO_BACKEND=stub` exercise this flow the same way it exercises software-dev-full in tests.
  **Alternatives considered**: hard-coding ClaudeCliBackend. Rejected — would make integration tests harder.

- **Decision**: Tests use a `_CannedBackend` (writes the canned verdict) rather than `StubBackend` (whose `_STUB_VERDICTS` registry doesn't know about `synthesize`).
  **Why**: Adding a new key to core's `_STUB_VERDICTS` would couple core to this pack's verdict schema, violating the layering principle.
  **Alternatives considered**: adding `synthesize` to core's stub registry — rejected for cross-layer coupling.
