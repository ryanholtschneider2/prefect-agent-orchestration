# Plan — prefect-orchestration-o2r

`agents/<role>/identity.toml` — auto-inject a `<self>` block into every
rendered prompt; per-rig overlay at `<rig>/.claude/agents/<role>/identity.toml`
takes precedence (per-field merge); identity.name flows through to
mcp-agent-mail `register_agent` via a new `{{agent_name}}` substitution.

## Affected files

- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/identity.py` (new) — `Identity` dataclass, `IdentityLoadError`, `load_identity()`, `format_self_block()`, `identity_vars()`.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/templates.py` — extend `render_template` with kwarg-only `rig_path: Path | None = None`; when an `identity.toml` resolves (pack default + optional rig overlay), prepend a `<self>...</self>` block before substitution and merge identity-derived vars (`{{agent_name}}`, `{{agent_email}}`, `{{agent_slack}}`, `{{agent_mail_name}}`, `{{agent_model}}`) behind caller-supplied kwargs.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/__init__.py` — re-export `Identity`, `IdentityLoadError`, `load_identity`, `format_self_block`, `identity_vars` for pack consumers.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_templates.py` — new tests for self-block, overlay precedence, missing-file backward-compat, identity vars, smoke.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_identity.py` (new) — pure-loader tests (TOML parsing, overlay merge, missing fields, malformed TOML, mail-name fallback).
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/engdocs/pack-convention.md` — add a "Per-role identity (o2r)" section before "Tool-access preference order" covering schema, file location, overlay precedence (per-field merge), `<self>` block format, and the `{{agent_name}}` / `register_agent` integration. Update the directory-layout fence to show `agents/<role>/identity.toml`.

## Approach

1. **Schema** (`identity.py`):

   ```toml
   [identity]
   name             = "acquisitions-bot"
   email            = "acquisitions@nanocorp.example"
   slack            = "@acquisitions-bot"
   mail_agent_name  = "acquisitions-bot"   # falls back to name when absent
   model            = "opus"
   ```

   All fields optional. `Identity` is a frozen dataclass with `name`,
   `email`, `slack`, `mail_agent_name`, `model` — all `str | None`.
   `effective_mail_agent_name` is a property (not a load-time default)
   to keep the loaded `Identity` round-trippable with on-disk content.

2. **Loader** (`load_identity(agents_dir, role, *, rig_path=None) -> Identity | None`):
   - Read `<agents_dir>/<role>/identity.toml` via stdlib `tomllib` if it exists.
   - If `rig_path` given, also try `<rig_path>/.claude/agents/<role>/identity.toml`.
   - **Overlay = per-field merge** (rig wins per key, pack fills the rest)
     — matches the "stack on top" overlay model already used for `overlay/**`
     and avoids forcing rigs to copy every field to override one.
   - Returns `None` when neither file exists (backward compat — no `<self>`
     block emitted, no errors).
   - Malformed TOML or non-string field values raise `IdentityLoadError`
     (subclass of `ValueError`) with the offending path; never silently
     swallowed.
   - Unknown keys in `[identity]` are ignored (forward-compat).
   - Tolerates flat-table TOML (no `[identity]` heading) for resilience.

3. **`format_self_block(identity) -> str`** — emits only present keys:

   ```
   <self>
   You are <name>.
   email: <email>
   slack: <slack>
   mail_agent_name: <mail_agent_name>
   model: <model>
   </self>

   ```

   Trailing blank line so it composes cleanly above the prompt body.
   Empty/None fields are omitted (line skipped). When identity carries
   no non-None fields, returns `""` (no empty wrapper).

4. **`identity_vars(identity) -> dict[str, str]`** — surfaces non-None
   fields as `agent_name`, `agent_email`, `agent_slack`,
   `agent_mail_name`, `agent_model`. Returns `{}` when input is `None`.

5. **`render_template` integration** (additive, kwarg-only):

   ```python
   def render_template(
       agents_dir: Path,
       role: str,
       *,
       rig_path: Path | None = None,
       **vars: object,
   ) -> str:
   ```

   - Load identity (pack + optional rig overlay).
   - If non-empty: prepend `format_self_block(identity)` to template body
     **before** `{{var}}` substitution (so substitution still applies inside
     the self block — useful if a future identity field references e.g.
     `{{issue_id}}`; trivially zero-cost when it doesn't).
   - Identity-derived vars merge **behind** caller-supplied kwargs
     (caller wins) so existing prompts that pass e.g. `agent_name` keep
     working unchanged.
   - Existing behavior (no `identity.toml` present) is unchanged: no
     prepended block, no auto-vars.

6. **Pack-side wiring** (`po_formulas.software_dev.render`): out of
   scope for this issue — the `_AGENTS_DIR` caller will need to pass
   `rig_path=…` to pick up the rig overlay, but that lives in the
   software-dev pack repo and is a one-line follow-up. ACs #1–#5 are
   all satisfiable by core + tests + docs alone (the smoke is a unit
   test, not a live pack invocation).

7. **`register_agent` integration** (AC #3): the orchestrator does not
   call `register_agent` directly today — that call is made by the
   agent itself, driven by the prompt convention documented in
   `pack-convention.md` ("Registers its identity ONCE at role entry…
   `register_agent … name="{{issue_id}}-{{role}}"`"). Satisfy AC #3 by
   exposing `{{agent_name}}` (sourced from `identity.name`) so prompts
   can write `name="{{agent_name}}"` instead of the legacy
   `{{issue_id}}-{{role}}` template, and documenting the recommended
   pattern in `pack-convention.md`. Existing prompts continue to work
   (legacy naming still valid until a follow-up updates the sw-dev pack).

8. **Docs** (`engdocs/pack-convention.md`): add a "Per-role identity"
   section before "Tool-access preference order" covering schema, file
   location, overlay precedence (rig > pack, per-field merge),
   `<self>` block format, and the `{{agent_name}}` / `register_agent`
   integration. Update the directory-layout fence to show the new
   `identity.toml` sibling of `prompt.md`.

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
| 1 | `tests/test_templates.py::test_identity_self_block_prepended` — write `agents_dir/<role>/{prompt.md, identity.toml}`, render, assert `<self>` block contains every present field and precedes prompt body. Backward-compat companion `test_no_identity_renders_unchanged`. |
| 2 | `tests/test_templates.py::test_rig_overlay_overrides_pack` (+ `test_identity.py::test_rig_overlay_per_field_merge`) — pack identity X, rig identity Y for one field; assert rendered `<self>` shows Y for that field and X for the others (per-field merge). |
| 3 | `tests/test_templates.py::test_agent_name_var_from_identity` — prompt uses `{{agent_name}}` (mimicking `register_agent name=`), identity.toml supplies `name=...`, assert substitution resolves. Companion `test_caller_var_overrides_identity` for caller-vs-identity precedence. `engdocs/pack-convention.md` documents the recommended `name="{{agent_name}}"` register pattern. |
| 4 | `engdocs/pack-convention.md` gains a "Per-role identity (o2r)" section with schema fence, overlay precedence subsection, and register_agent integration subsection — verifiable by `grep "Per-role identity"` and structural inspection during plan-critic review. |
| 5 | `tests/test_templates.py::test_smoke_overlay_precedence_in_rendered_prompt` — full end-to-end loader: pack agents_dir with identity X, rig overlay with identity Y at `<rig>/.claude/agents/<role>/identity.toml`, render with `rig_path=<rig>`, assert rendered body contains Y's name in the `<self>` block and that `{{agent_mail_name}}` substitution resolves to Y (mail_agent_name fallback to overridden name). |

## Test plan

- **unit only**: `tests/test_identity.py` (loader + helpers) + new
  identity-aware sections in `tests/test_templates.py` (renderer
  integration). No e2e — this ships zero new CLI surface, zero
  subprocess calls, zero Prefect flow changes. The sw-dev pack hookup
  (passing `rig_path=…`) is the natural e2e seam and lives in a
  follow-up bead.
- **No playwright** (no UI surface).

## Risks

- **Signature change to `render_template`**: kwarg-only `rig_path` keeps
  positional callers (the only in-tree caller, `po_formulas.software_dev.render`,
  forwards `**vars`) working unchanged. No breaking consumers.
- **Auto-injected vars colliding with existing prompt vars**: caller-
  supplied vars win; identity-derived vars merge behind. Documented in
  the `render_template` docstring.
- **`<self>` block colliding with prompt body**: prompts that already
  hand-roll a `<self>` block would render two. None do today (grep is
  empty across in-tree packs); add a doc note that hand-rolled `<self>`
  blocks should be removed once identity.toml is present.
- **TOML parse errors**: surfaced loudly (`IdentityLoadError`) rather
  than silently dropped — explicit choice; identity is identity, a
  malformed file should not silently render an anonymous prompt.
- **Per-field merge vs full replacement**: merge is the "less surprising"
  default (rigs override one field, not all of them). Documented
  explicitly in `pack-convention.md` with an example.
- **No DB / API contract changes** — config files plus an additive
  kwarg-only signature change; no migrations.
