# JSON Output Schemas for PO CLI

All commands accept `--json` to emit machine-parseable output instead of the
default human-formatted table. Shapes are stable — new optional fields may be
added in future; existing fields are not removed or renamed without a major
version bump.

---

## `po list --json`

Returns a JSON array. Each element:

```json
{
  "kind": "formula",          // "formula" | "command"
  "name": "software-dev-full", // entry-point name
  "module": "po_formulas.software_dev:software_dev_full",
  "doc": "Run the actor-critic pipeline on a single bead."
}
```

---

## `po show <name> --json`

Returns a single JSON object:

```json
{
  "kind": "formula",          // "formula" | "command"
  "name": "software-dev-full",
  "module": "po_formulas.software_dev",
  "callable": "software_dev_full",
  "signature": "(issue_id: str, rig: str, rig_path: pathlib.Path, ...)",
  "doc": "Full docstring text (multi-line)."
}
```

---

## `po status --json`

Returns a JSON array. Each element:

```json
{
  "issue_id": "prefect-orchestration-5i9",
  "rig": "prefect-orchestration",
  "run_id": "a1b2c3d4",      // first 8 chars of Prefect flow-run UUID
  "flow_name": "prefect-orchestration-5i9",
  "state": "Running",
  "started": "2026-04-29T12:00:00+00:00",  // ISO-8601, nullable
  "ended": null,               // ISO-8601 or null
  "current_step": "builder",  // nullable — latest non-terminal task name
  "run_count": 1               // total flow runs for this issue
}
```

---

## `po sessions <issue-id> --json`

Returns a JSON array. Each element:

```json
{
  "role": "builder",
  "uuid": "f7e2a1b3-...",
  "last_iter": "2",            // string; "-" if no iter artifacts
  "last_updated": "2026-04-29 12:34:56",
  "pod": null                  // k8s pod name or null
}
```

---

## `po watch <issue-id> --json`

Emits NDJSON (one JSON object per line). Each line:

```json
{"ts": "2026-04-29T12:00:00.123456+00:00", "source": "prefect", "kind": "state", "text": "Pending → Running  (my-flow)"}
```

Fields:
- `ts`: ISO-8601 UTC datetime
- `source`: `"prefect"` | `"run-dir"` | `"internal"`
- `kind`: `"state"` | `"task"` | `"new"` | `"modified"` | `"replay"` | `"info"` | `"replay-separator"`
- `text`: human-readable body

When `--replay` is used, the `REPLAY_SEPARATOR` (`===== live =====`) is
emitted as a JSON object:

```json
{"ts": "...", "source": "internal", "kind": "replay-separator", "text": "===== live ====="}
```

---

## `po spend --json`

Returns a JSON array of raw per-role spend records:

```json
{
  "formula": "software-dev-full",
  "issue_id": "prefect-orchestration-5i9",
  "role": "builder",
  "model": "claude-sonnet-4-6",
  "day": "2026-04-29",        // YYYY-MM-DD, derived from run_dir mtime
  "in_tok": 125000,
  "out_tok": 8500,
  "cache_r_tok": 95000,
  "cache_w_tok": 30000,
  "cost_usd": 0.4275,
  "pricing_note": "Prices are estimated (per-MTok, USD) based on hardcoded table; subject to Anthropic pricing changes. Not billing-grade."
}
```

`po spend` (no `--json`) prints a grouped table using `--by` (default: `role`).
Valid values for `--by`: `formula`, `role`, `day`.
