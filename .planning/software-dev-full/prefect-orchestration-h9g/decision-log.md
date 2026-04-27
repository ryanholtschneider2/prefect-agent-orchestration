# Decision log — prefect-orchestration-h9g (build iter 1)

- **Decision**: Removed the positional `name: str = typer.Argument(...)` from `cli.run` and instead pull the formula name out of `ctx.args` manually (first non-`--` token).
  **Why**: With `context_settings={"allow_extra_args": True, "ignore_unknown_options": True}`, Click greedily fills the `name` Argument with the next non-option token — which, when `--from-file path --a 2` was passed, became the unknown `--a` flag (or `2`), corrupting both `name` and the kwargs. Manual extraction sidesteps the interleaving problem entirely.
  **Alternatives considered**: (a) leaving `name` as Argument and patching after-the-fact — fragile because the leftover ctx.args order is already destroyed by Click; (b) requiring `--from-file` users to write `po run "" --from-file …` — ugly UX.

- **Decision**: Synthetic module name is `po_scratch_<sha1(abspath)[:10]>`.
  **Why**: Stable across reloads of the same path (idempotent re-runs return the same Flow object), distinct across different paths, and safe as a Python identifier. Hash-of-abspath instead of hash-of-content so editing the file in place produces a fresh import only after restart, matching the principle-of-least-surprise — a single `po run` invocation always sees one snapshot.
  **Alternatives considered**: stem-based (`po_scratch_<basename>`) — collides on duplicate basenames; UUID — non-idempotent.

- **Decision**: Insert into `sys.modules` *before* `exec_module`, and pop on failure.
  **Why**: Standard import-system contract — modules that reference themselves (`from <self> import …`, dataclass forward refs) need the entry visible during execution. Popping on failure keeps `sys.modules` clean so the second invocation gets a fresh attempt.
  **Alternatives considered**: assigning after exec_module — breaks self-referential imports.

- **Decision**: Detect Prefect flows via `isinstance(obj, prefect.flows.Flow)`, lazy-imported.
  **Why**: Prefect is already a hard dep of PO. Lazy import keeps `scratch_loader` cheap when nobody uses `--from-file`. Duck-typing on `__prefect_flow__` would also work but the official Flow class import is the documented public API.

- **Decision**: When both a positional formula name and `--from-file` are passed, hard-error with exit 2 instead of letting `--from-file` win silently.
  **Why**: Triage flagged "explicit beats implicit" but explicit-wins-with-warning surprises the user (their typed name is silently ignored). Loud failure is the kinder default for a dev tool.
  **Alternatives considered**: implicit precedence with stderr warning — easier to miss in agent transcripts.

- **Decision**: Did NOT add `name` Option (`--name`) for the registered-formula path; kept `--name` strictly for in-file flow disambiguation under `--from-file`.
  **Why**: Plan said `[--name <flow-name>]` as the per-file disambiguator only. Avoids overloading.
