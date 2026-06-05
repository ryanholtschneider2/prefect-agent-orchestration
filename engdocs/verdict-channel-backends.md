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
`agent_step` derives a deterministic iter id (`<seed>.<step>.iter<N>`). As of
9xa.1 the slow-path adopts `create_child_bead`'s return value as the canonical
`target_bead`: on bd the explicit id is honored and the return matches the
input, on br the backend-minted id is threaded through the description stamp,
the convergence-ladder status probes, and the verdict read. The computed id is
only ever the *requested* id passed into `create_child_bead`; everything
downstream targets the real bead.

### Idempotency across calls — the convention→real-id map (`prefect-orchestration-99k`)

9xa.1 only adopted the id *within a single call*. `agent_step` is stateless
across calls, so the next re-entry for the same role-step recomputed the
phantom convention id, missed the fast-path cache (`ISSUE_NOT_FOUND` on br),
and `create_child_bead` minted *another* fresh bead — re-dispatching
already-completed iters forever and re-nudging the agent about a bead that
doesn't exist (observed in the br smoke: the builder closed its real `bd-22q`
but kept being asked to close the phantom `bd-3ih.build.iter3`).

`prefect_orchestration/iter_bead_ids.py` closes the gap with a best-effort
run-dir-scoped map (`<run_dir>/iter-bead-ids.json`) of convention id →
backend-assigned id. `agent_step` consults it before the fast-path probe (so
re-entry resolves the real bead and the cache short-circuits — no re-mint, no
phantom re-nudge) and records the mapping after `create_child_bead` adopts a
divergent id. On dolt the convention id is honored, so the lookup misses and
the caller falls back to it — byte-identical behavior, no map written.
`context_bundle.build_context_md` consults the same map (and accepts an
explicit `iter_bead_id`) and resolves its `show` through `_resolve_binary`, so
the agent-facing CONTEXT.md "This role-step" section shows the real bead on br
instead of an empty phantom lookup.

Close-the-loop coverage lives at
`tests/e2e/test_iter_bead_ids_br_roundtrip.py` — it drives the *real* `br`
binary against a real rig and asserts the symptom is gone: br mints a flat id
the dotted convention id can't resolve, the recorded map makes re-entry target
that real id, and the rig ends with exactly seed + one iter bead (no
phantom-triggered re-mint, iter count back to 1). Skipped when `br` is off
PATH; the unit layer (`tests/test_agent_step.py`,
`tests/test_iter_bead_ids.py`) covers the same logic with mocks.

## Still deferred

- **`BeadsStore` metadata bus** — `get`/`set`/`all` still use
  `bd … --set-metadata` for non-verdict state (`po.run_dir`, `po.rig_path`,
  `po.iter_cap`). br has no per-issue metadata at all; re-homing those keys
  (likely onto comments, like the verdict channel) is its own slice.
- **Pack agent prompts** (`po-formulas-software-dev`, a separate repo / PR;
  tracked as bead **prefect-orchestration-ysw**) — roles still emit
  `bd update <id> --metadata` to write verdicts. They need to emit the br form
  (`br comments add <id> 'po-verdict:<role>:<json>'`) on a br rig, ideally
  routed through `beads_backend.write_verdict` so the prompt stays
  backend-agnostic. Until this lands, a full `software_dev_full` run does **not**
  work end-to-end on br: roles can't record their verdicts.
- **Pack graph-mode reconstruction** (`po-formulas-software-dev`, separate
  repo) — `software_dev.py`'s watcher loop still discovers / counts iters by
  scanning `bd list` for `<seed>.<step>.iter<N>` ids, which never exist on br.
  The core `prefect-orchestration-99k` map makes `agent_step` itself idempotent
  (re-entry no longer re-mints) and exposes the real ids at
  `<run_dir>/iter-bead-ids.json`; the pack still needs to read that map (rather
  than the convention-id scan) and pass `iter_bead_id` into `build_context_md`
  so the graph-mode loop converges on br. Tracked as a pack-side follow-up.
