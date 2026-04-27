# Plan — prefect-orchestration-o2r

`agents/<role>/identity.toml` — auto-inject a `<self>` block into every
rendered prompt; per-rig overlay at `<rig>/.claude/agents/<role>/identity.toml`
takes precedence; identity.name flows through to mcp-agent-mail
`register_agent` via a new `{{agent_name}}` substitution.

## Affected files

- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/identity.py` (new) — `Identity` dataclass, `load_identity()`, `format_self_block()`.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/templates.py` — extend `render_template` with kwarg-only `rig_path: Path | None = None`; if present (or pack default exists), prepend a `<self>...</self>` block before substitution and inject identity fields as auto-vars (e.g. `{{agent_name}}`, `{{agent_email}}`, `{{agent_slack}}`).
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/__init__.py` — re-export `load_identity`, `Identity` for pack consumers.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_templates.py` — new tests for self-block, overlay precedence, missing-file backward-compat, identity vars.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_identity.py` (new) — pure-loader tests (TOML parsing, overlay merge, missing fields).
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/engdocs/pack-convention.md` — add schema + overlay precedence section under "Per-agent secrets" (or a new top-level "Per-role identity" section). Update the directory-layout block to show `agents/<role>/identity.toml`.

## Approach

1. **Schema** (`identity.py`):

   ```toml
   [identity]
   name = "acquisitions-bot"
   email = "acquisitions@nanocorp.example"
   slack = "@acquisitions-bot"
   mail_agent_name = "acquisitions-bot"   # falls back to name
   model = "opus"
   ```

   All fields optional. `Identity` is a frozen dataclass with `name`,
   `email`, `slack`, `mail_agent_name`, `model` (all `str | None`).
   `mail_agent_name` defaults to `name` when absent.

2. **Loader** (`load_identity(agents_dir, role, rig_path=None) -> Identity | None`):
   - Read `<agents_dir>/<role>/identity.toml` via stdlib `tomllib` if it exists.
   - If `rig_path` given, also try `<rig_path>/.claude/agents/<role>/identity.toml`.
   - **Overlay = per-field merge** (rig wins per key, pack fills the rest)
     — matches the "stack on top" overlay model already used for `overlay/**`
     and avoids forcing rigs to copy every field to override one.
   - Returns `None` when neither file exists (backward compat — no `<self>`
     block emitted, no errors).
   - Malformed TOML raises `IdentityLoadError` (subclass of `ValueError`)
     with the offending path; never silently swallowed.

3. **`format_self_block(identity) -> str`** — emits only present keys:

   ```
   <self>
   You are <name>.
   email: <email>
   slack: <slack>
   mail_agent_name: <mail_agent_name>
   </self>

   ```

   Trailing blank line so it composes cleanly above the prompt body.
   Empty/None fields are omitted (line skipped). When identity has no
   non-None fields, return `""`.

4. **`render_template` integration** (additive, kwarg-only):

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
     **before** `{{var}}` substitution (so `{{var}}` substitution still
     applies inside the self block — useful if a future identity field
     references e.g. `{{issue_id}}`; trivially zero-cost when it doesn't).
   - Inject identity fields as additional vars *only if not explicitly
     passed by caller* (caller wins): `agent_name`, `agent_email`,
     `agent_slack`, `agent_mail_name`, `agent_model`. Missing identity
     fields fall through to the existing `KeyError` if the prompt
     references them.
   - Existing behavior (no `identity.toml` present) is unchanged: no
     prepended block, no auto-vars.

5. **Pack-side wiring** (`po_formulas.software_dev.render`): out of
   scope for this issue — the `_AGENTS_DIR` caller will need to pass
   `rig_path=…` to pick up the rig overlay, but that lives in the
   software-dev pack and is a one-line follow-up. This issue ships the
   core seam; the pack-side patch is `o2r`'s natural successor and not
   required for AC #1–#4.

6. **`register_agent` integration** (AC #3): the orchestrator does not
   call `register_agent` directly today — that call is made by the
   agent itself, driven by the prompt convention documented in
   `pack-convention.md` ("Registers its identity ONCE at role entry…
   `register_agent … name="{{issue_id}}-{{role}}"`"). Satisfy AC #3 by:
   - Exposing `{{agent_name}}` (sourced from `identity.name`, falling
     back to existing prompt-supplied vars when no identity present)
     so prompts can write `name="{{agent_name}}"` instead of
     `name="{{issue_id}}-{{role}}"`.
   - Documenting the rename in `pack-convention.md` as the recommended
     pattern; existing prompts continue to work (issue+role naming is
     still valid until a follow-up updates the sw-dev pack).

7. **Docs** (`engdocs/pack-convention.md`): add a "Per-role identity"
   section between "Overlay" and "Tool-access preference order"
   covering schema, file location, overlay precedence (rig > pack,
   per-field merge), `<self>` block format, and the
   `{{agent_name}}` / `register_agent` integration.

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
| 2 | `tests/test_templates.py::test_rig_overlay_overrides_pack` — write pack identity X, rig identity Y for one field; assert rendered `<self>` shows Y for that field and X for the others (per-field merge). |
| 3 | `tests/test_templates.py::test_agent_name_var_from_identity` — prompt uses `{{agent_name}}` (mimicking the `register_agent name=` line), identity.toml supplies `name=...`, assert substitution resolves. Companion `test_caller_var_overrides_identity` for precedence. Documentation in `pack-convention.md` updated to recommend `{{agent_name}}` for `register_agent` calls. |
| 4 | grep `engdocs/pack-convention.md` for "Per-role identity" + schema fence + "rig overlay" precedence wording (manual review during plan-critic; structural assertion implicit in lint of the markdown). |
| 5 | `tests/test_templates.py::test_smoke_overlay_precedence_in_rendered_prompt` — full end-to-end: pack agents_dir with identity X, rig overlay with identity Y at `<rig>/.claude/agents/<role>/identity.toml`, render with `rig_path=<rig>`, assert rendered body contains Y's name in the `<self>` block. |

## Test plan

- **unit only** (`tests/test_identity.py` for the loader, additions to
  `tests/test_templates.py` for renderer integration). No e2e — this
  ships zero new CLI surface, zero subprocess calls, zero Prefect
  flow changes. The sw-dev pack hookup (passing `rig_path=…`) is the
  e2e seam; it ships in a follow-up bead.
- No playwright (no UI).

## Risks

- **Signature change to `render_template`**: kwarg-only `rig_path` keeps
  positional callers (the only caller, `po_formulas.software_dev.render`,
  forwards `**vars`) working unchanged. No breaking consumers.
- **Auto-injected vars colliding with existing prompt vars**: caller-
  supplied vars win; identity-derived vars are only added when not
  already in `vars`. Documented in `render_template` docstring.
- **`<self>` block colliding with prompt body**: prompts that already
  hand-roll a `<self>` block would see two. None do today (grep is
  empty across packs); add a doc note that hand-rolled `<self>` blocks
  should be removed once identity.toml is present.
- **TOML parsing errors**: surfaced loudly (`IdentityLoadError`) rather
  than silently dropped — explicit choice; identity is identity, a
  malformed file should not silently render an anonymous prompt.
- **Per-field merge vs full replacement**: merge is the "less surprising"
  default (rigs override one field, not all of them). Documented
  explicitly in `pack-convention.md` with an example.
- **No DB / API contract changes** — config files and a kwarg-only
  signature addition.
