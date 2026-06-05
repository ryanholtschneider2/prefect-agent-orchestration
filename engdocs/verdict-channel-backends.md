# Verdict-channel backends (`beads_backend`)

PO's verdict channel is the path by which a role's structured result reaches
the orchestrator: an agent writes a verdict, and `parsing.read_bead_verdict`
reads it back. Historically this was dolt-specific — agents stamped
`bd update <iter> --metadata '{"po.<role>": ...}'` and the orchestrator read
`metadata["po.<role>"]`. `beads_rust` (`br`: Rust, SQLite-WAL, JSONL, no
server) has **no per-issue arbitrary metadata**, so adopting br requires
re-homing the channel. `prefect_orchestration/beads_backend.py` is the seam.

## Backends

| | dolt (default) | br (`beads_rust`) |
|---|---|---|
| binary | `bd` | `br` |
| write verdict | `bd update <id> --set-metadata po.<name>=<json>` | `br comments add <id> 'po-verdict:<name>:<json>'` (append-only) |
| read verdict | `metadata["po.<name>"]` | latest `po-verdict:<name>:` comment by `max(id)` |
| dep rows | `{"id","status","title",…}` | `{"issue_id","depends_on_id","status","title",…}` (re-keyed) |
| `.beads/metadata.json` | has `dolt_mode` | has `database` + `jsonl_export`, no `dolt_mode` |

Append-only comments (not a single rewritten blob) avoid the read-modify-write
race that concurrent roles would otherwise hit. The br comment `id` is a
monotonic integer, so "latest verdict" is `max(id)` over the matching comments
— no timestamp parsing.

## Backend selection — `resolve_backend(rig_path)`

Precedence:

1. `PO_BEADS_BACKEND=dolt|br` env override (anything else is ignored).
2. Sniff `<rig_path>/.beads/metadata.json`: `dolt_mode` present → `dolt`;
   `database` + `jsonl_export` and no `dolt_mode` → `br`.
3. Default `dolt` (also when the file is absent/unreadable). Safe — preserves
   today's behaviour for every non-br rig.

## What this bead landed vs. deferred

Landed (the verifiable core seam):

- `beads_backend.read_verdict` / `write_verdict` round-trip a verdict against a
  **real br workspace** (gated unit tests run when `br` is on PATH).
- `parsing._bd_show_once` delegates to the seam; the `read_bead_verdict`
  retry/timeout/cached-fallback wrapper is byte-for-byte unchanged, so the dolt
  read path is behavior-identical.
- `beads_meta._bd_dep_list` / `_bd_show` resolve the backend binary and run br
  dep rows through `normalize_dep_rows`, so `resolve_seed_bead` / `list_subgraph`
  work against br.
- `doctor.check_beads_dolt_mode` reports SQLite-WAL health for a br rig and is
  unchanged for dolt rigs.

Deferred (cross-repo follow-up): threading `br` through the *write*-side
`beads_meta` shellouts (`claim_issue`/`close_issue`/`create_child_bead`/
`mint_seed_bead`) and updating the pack's agent prompts
(`po-formulas-software-dev`) to emit `br comments add`. A literal full
`software_dev_full`-against-br run needs both.
