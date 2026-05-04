- **Decision**: Added `planning-init` as a pack command instead of a new `po run` formula.
  **Why**: The plan and pack conventions call for simple planning-artifact scaffolding without adding a new orchestration primitive.
  **Alternatives considered**: A dedicated planning formula in core or in the pack.

- **Decision**: Fail the scaffold command on any existing target file instead of partially updating artifacts.
  **Why**: The issue explicitly prioritizes overwrite safety for user-authored planning documents.
  **Alternatives considered**: Silent overwrite, per-file skip behavior, or merge/update logic.
