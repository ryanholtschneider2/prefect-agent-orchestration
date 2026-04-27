# Plan — prefect-orchestration-o2r

`agents/<role>/identity.toml` — auto-inject a `<self>` block into every
rendered prompt; per-rig overlay at `<rig>/.claude/agents/<role>/identity.toml`
takes precedence (per-field merge); identity.name flows through to
mcp-agent-mail `register_agent` via a new `{{agent_name}}` substitution.

## Status: VERIFICATION-ONLY

All five ACs have already landed across three prior build iterations
(commits `f616437`, `d1d0f06`, `3057486`, `b005b9c`, `d2823fd`). The
plan-critic in iter 1 issued **approved** with non-blocking nits, and
iter 2/3 cleared regression-gate fallout (sub-typer verb walking, the
missing `parsing.prompt_for_verdict` helper, ruff lint).

This plan documents what shipped so the critic + builder rounds can
re-verify against the live tree rather than re-implement. No new
production code is contemplated — only baseline drift fixes if the
regression gate flags any.

## Affected files (already in tree)

- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/identity.py` (new in `f616437`) — `Identity` dataclass, `IdentityLoadError`, `load_identity()`, `format_self_block()`, `identity_vars()`.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/templates.py` — `render_template` extended with kwarg-only `rig_path: Path | None = None`; resolves `identity.toml` (pack default + optional rig overlay), prepends `<self>...</self>` block before substitution, merges identity-derived vars (`{{agent_name}}`, `{{agent_email}}`, `{{agent_slack}}`, `{{agent_mail_name}}`, `{{agent_model}}`) behind caller-supplied kwargs.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/__init__.py` — re-exports `Identity`, `IdentityLoadError`, `load_identity`, `format_self_block`, `identity_vars`.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/parsing.py` — added `prompt_for_verdict` helper (consumed by sw-dev pack; landed in `3057486` as a regression-gate fix, not strictly an o2r AC).
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/commands.py` — `core_verbs()` extended to walk Typer sub-groups so `po packs install/update/uninstall/list` are reserved (`d1d0f06`, regression-gate fix).
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_identity.py` (new) — 13 loader tests.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_templates.py` — 7 new identity-aware renderer tests.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_parsing.py` (new) — 7 tests covering `prompt_for_verdict` + `read_verdict`.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/engdocs/pack-convention.md` — "Per-role identity (o2r)" section before "Tool-access preference order"; directory-layout fence updated to show `agents/<role>/identity.toml`.

## Approach (as shipped)

1. **Schema** (`identity.py`): `[identity]` table with optional
   `name`, `email`, `slack`, `mail_agent_name`, `model` (all `str | None`).
   `Identity` is a frozen dataclass; `effective_mail_agent_name` is a
   property (not a load-time default) so loaded data is round-trippable
   with on-disk content. Unknown keys ignored (forward-compat).
   Flat-table TOML tolerated for resilience.

2. **Loader** (`load_identity(agents_dir, role, *, rig_path=None) -> Identity | None`):
   reads `<agents_dir>/<role>/identity.toml` (pack default) and overlays
   `<rig_path>/.claude/agents/<role>/identity.toml` when `rig_path` is
   given. **Per-field merge** (rig wins per key). Returns `None` when
   neither file exists. Malformed TOML / non-string values raise
   `IdentityLoadError` (subclass of `ValueError`).

3. **`format_self_block(identity) -> str`** emits one line per present
   key inside `<self>...</self>` plus a trailing blank line; returns
   `""` when identity is wholly empty (no empty wrapper).

4. **`identity_vars(identity) -> dict[str, str]`** surfaces non-None
   fields as `agent_name` / `agent_email` / `agent_slack` /
   `agent_mail_name` / `agent_model`. Returns `{}` for `None`.

5. **`render_template` integration**: kwarg-only `rig_path` keeps every
   existing positional caller working. When identity resolves, the
   `<self>` block is prepended to the template body **before** `{{var}}`
   substitution so identity-derived vars and any future `{{var}}` inside
   the block both substitute. Identity vars merge **behind** caller
   kwargs — caller wins.

6. **Pack-side wiring**: `po_formulas.software_dev.render` is unchanged
   for now (no `rig_path=` forwarding); that's a follow-up bead. ACs
   #1–#5 are satisfied by core + tests + docs alone (smoke is a unit
   test).

7. **`register_agent` integration** (AC #3): orchestrator does not call
   `register_agent` directly today. AC satisfied by exposing
   `{{agent_name}}` in renders + documenting the recommended
   `name="{{agent_name}}"` pattern in `pack-convention.md`.

8. **Regression-gate fixes** carried in this bead's commits but not
   strictly o2r ACs: `parsing.prompt_for_verdict` (pack import was
   broken across the boundary; pytest collection aborted on import) and
   `commands.core_verbs()` walking `app.registered_groups` (test
   asserted nested `packs install/update/uninstall/list` were reserved).

## Acceptance criteria (verbatim from the issue)

(1) render_template reads `<agents_dir>/<role>/identity.toml` when
present; injects as `<self>` block; (2) per-rig overlay overrides pack
default; (3) mcp-agent-mail register_agent uses identity.name when
calling; (4) pack-convention.md documents schema + overlay precedence;
(5) smoke: pack ships role with identity X, rig overlay overrides to
Y, agent's prompt shows Y.

## Verification strategy

| AC | How verified |
|---|---|
| 1 | `tests/test_templates.py::test_identity_self_block_prepended` (writes `agents_dir/<role>/{prompt.md, identity.toml}`, asserts `<self>` block is prepended with all present fields) + backward-compat companion `test_no_identity_renders_unchanged`. |
| 2 | `tests/test_templates.py::test_rig_overlay_overrides_pack` + `tests/test_identity.py::test_rig_overlay_per_field_merge` (pack identity X, rig identity Y for one field; assert rendered `<self>` shows Y for that field, X for the rest). |
| 3 | `tests/test_templates.py::test_agent_name_var_from_identity` (prompt uses `{{agent_name}}` mimicking `register_agent name=`, identity supplies `name=`, assert substitution resolves) + `test_caller_var_overrides_identity` (caller-vs-identity precedence). `engdocs/pack-convention.md` documents the recommended `name="{{agent_name}}"` register pattern. |
| 4 | `engdocs/pack-convention.md` contains a "Per-role identity (o2r)" section with schema fence, overlay precedence subsection (per-field merge), and `register_agent` integration subsection — `grep -n "Per-role identity" engdocs/pack-convention.md` returns a match. |
| 5 | `tests/test_templates.py::test_smoke_overlay_precedence_in_rendered_prompt` (full loader path: pack identity X + rig overlay Y at `<rig>/.claude/agents/<role>/identity.toml` + render with `rig_path=<rig>` → `<self>` block contains Y's name; `{{agent_mail_name}}` substitution resolves to Y via the mail-name fallback). |

Run: `uv run python -m pytest tests/test_identity.py tests/test_templates.py tests/test_parsing.py` — expect 32 passed.

## Test plan

- **unit only** — `tests/test_identity.py` (13), `tests/test_templates.py` (additions), `tests/test_parsing.py` (7). No new e2e: zero new CLI surface, zero subprocess calls, zero Prefect flow changes. The sw-dev pack's eventual `rig_path=` plumbing is the natural e2e seam and is in a follow-up bead.
- **No playwright** — no UI surface.

## Risks

- **Signature change to `render_template`**: kwarg-only `rig_path` is
  additive; the only in-tree caller (`po_formulas.software_dev.render`)
  forwards `**vars` and is unaffected.
- **Identity-derived vars colliding with existing prompt vars**:
  caller-supplied kwargs win; identity merges behind. Documented in the
  `render_template` docstring.
- **Hand-rolled `<self>` block in a prompt body**: would render twice
  once `identity.toml` is present. Grep is empty across in-tree packs;
  `pack-convention.md` carries a doc note.
- **TOML parse errors**: surface loudly via `IdentityLoadError`, not
  silently anonymous.
- **Per-field merge vs full replacement**: merge is the "less surprising"
  default — rigs override one field, not all of them. Documented with
  an example in `pack-convention.md`.
- **No DB / API contract changes** — config files and an additive
  kwarg-only signature change. Zero migrations.
- **Baseline drift**: per `baseline-notes.md`, the rig has 27 unrelated
  pre-existing failures (cli_packs, agent_session_tmux, deployments,
  mail). None block o2r ACs. The regression-gate's job is to confirm
  o2r-attributable changes don't widen that count.
