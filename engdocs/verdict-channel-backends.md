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

## Write-side shellouts (`prefect-orchestration-9xa.1`)

The four `beads_meta` write helpers resolve `bd` vs `br` through
`_resolve_binary(rig_path)` (same backend selection as the read side) instead
of hardcoding `bd`:

| helper | dolt (`bd`) | br |
|---|---|---|
| `claim_issue` | `bd update <id> --status in_progress --assignee <a>` | same, binary swap (flag-identical) |
| `close_issue` | `bd close <id> [--reason <r>]` | same, binary swap (flag-identical) |
| `create_child_bead` | `bd create --id=<child> --title … --deps parent-child:<p>` → returns `child_id` | `br create <title-positional> … --deps parent-child:<p> --json` → returns the **br-assigned id** |
| `mint_seed_bead` | `bd create [--id=…] --title … --label <l>` | `br create <title-positional> … --labels <l> --json` → returns the br-assigned id |

A missing binary is handled exactly as a missing `bd` was: `claim_issue` /
`close_issue` no-op; `create_child_bead` / `mint_seed_bead` raise
`NotImplementedError`.

### br create has no explicit id — the one behavioral gap

`br create` mints its own id (there is no `--id` flag), so on br
`create_child_bead` / `mint_seed_bead` **cannot honor a caller-chosen id** and
return the br-assigned id parsed from `--json` instead. Two consequences:

1. **Callers must use the return value**, not the requested `child_id`. The
   real-br round-trip in `tests/test_beads_meta.py` asserts the returned id
   differs from the requested one.
2. **No idempotency by requested id on br.** bd treats an `--id` collision as a
   no-op (retry-safe); br can't look a bead up by a caller-chosen id, so a
   retry mints a fresh bead. Dedupe on the returned id.

This matters for a *literal* full `software_dev_full`-against-br run because
`agent_step` derives a deterministic iter id via
`beads_meta.iter_bead_id(seed, step, iter_n)` → `<seed>-<step>-iter<N>`
(prefect-orchestration-5w3 moved this off the legacy dot-separated
`<seed>.<step>.iter<N>`, which br rejects). As of
9xa.1 the slow-path adopts `create_child_bead`'s return value as the canonical
`target_bead`: on bd the explicit id is honored and the return matches the
input, on br the backend-minted id is threaded through the description stamp,
the convergence-ladder status probes, and the verdict read. The computed id is
only ever the *requested* id passed into `create_child_bead`; everything
downstream targets the real bead. (br is still not idempotent by requested id —
a retry/resume mints a fresh iter bead because the cache probe on the computed
id never hits on br. Acceptable for a forward run; revisit if br resume becomes
a requirement.)

## Landed by prefect-orchestration-5w3

- **Iter-bead ids are br-safe.** `beads_meta.iter_bead_id` / `iter_bead_re` are
  the single source of truth for the `<seed>-<step>-iter<N>` convention;
  `agent_step`, `context_bundle`, `resume`, `artifacts`, and the pack's
  triage-flag read + terminal-iter scan + `summarize-verdicts` all route through
  them. No dot-separated iter id is constructed anywhere.
- **Write side is backend-agnostic.** Role prompts emit
  `po write-verdict <bead> <role> '<json>'` (the pack's `po.commands` entry,
  which calls `beads_backend.write_verdict`) instead of hardcoding
  `bd update <id> --metadata`. On dolt it stamps `metadata["po.<role>"]`; on br
  it appends a `po-verdict:<role>:<json>` comment. With this + the read side
  (`parsing.read_bead_verdict`), a full `software_dev_full` run records and reads
  verdicts on either backend.
- **Migration helper.** `setup/migrate-dolt-to-br.sh` (+ the testable
  `setup/migrate_jsonl_ids.py`) rewrites dotted iter ids to hyphens in a dolt
  JSONL export before `br sync --import-only`.

## Still deferred

- **`BeadsStore` metadata bus** — `get`/`set`/`all` still use
  `bd … --set-metadata` for non-verdict state (`po.run_dir`, `po.rig_path`,
  `po.iter_cap`). br has no per-issue metadata at all; re-homing those keys
  (likely onto comments, like the verdict channel) is its own slice.
- **Graph-mode iter ids** — `graph.py` / `per_role_step` still mint
  `<child>.iter<N>` (a separate dot convention from the `agent_step` lineage).
  Moving graph mode to the hyphen helper is out of scope for 5w3 (which scoped
  to the `agent_step` / `software_dev` path) and wants its own bead before a
  graph-mode-on-br run.
