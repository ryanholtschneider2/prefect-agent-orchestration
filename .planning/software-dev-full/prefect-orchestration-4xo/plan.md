# Plan — prefect-orchestration-4xo

`agents/<role>/memory/` loader — auto-prepend `MEMORY.md` as `<memory>` block on every rendered prompt.

## Affected files

- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/templates.py` — add memory-block prepend.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_templates.py` — unit tests for new behavior (load, no-memory backward compat, ordering with `<self>`, two-turn smoke).
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/engdocs/pack-convention.md` — document `agents/<role>/memory/MEMORY.md` convention.

No code changes outside core; no callers need updating (templating layer is a single function).

## Approach

`render_template(agents_dir, role, *, rig_path=None, **vars)` currently composes:

```
<self>...</self>  (optional, from identity.toml)
<prompt body>
```

Add a third source: per-role memory. Resolution mirrors identity overlay precedence (pack default + per-rig override merge):

1. **Pack default**: `<agents_dir>/<role>/memory/MEMORY.md`.
2. **Rig overlay** (if `rig_path` provided): `<rig_path>/.claude/agents/<role>/memory/MEMORY.md`.
   - Per AC the literal "agents/<role>/memory/" convention only requires the pack path. But for consistency with identity overlay and to support per-rig memory authoring (which is the natural fit — agents WRITE memory at runtime, and the rig is the writable, run-local location), we **prefer** the rig overlay when present and fall back to the pack default.
   - Decision: load **rig** if it exists, else **pack**, else nothing. This is "rig wins" file-level precedence (not per-line merge — MEMORY.md is unstructured prose). Documented as such in pack-convention.md. This shape matches how Claude Code itself stores memory at `~/.claude/projects/<slug>/memory/` rather than at the pack/install location.
3. If neither exists, behave exactly as today (AC2 backward-compat).
4. If a memory file is found, prepend its **raw contents** wrapped as:

   ```
   <memory>
   <…file contents verbatim…>
   </memory>

   ```

   (trailing blank line so the next block — `<self>` or prompt body — starts cleanly).

5. Final ordering of the rendered string: `<memory>` → `<self>` → prompt body. Memory is the most "static" / oldest context, identity is per-role config, body is the work prompt — outermost-first matches how Claude Code itself layers system memory above project instructions. (`AgentSession.prompt()` later prepends `<mail-inbox>` per turn, so the *delivered* prompt becomes `<mail-inbox>` → `<memory>` → `<self>` → body. Documented.)

6. No `{{var}}` substitution inside the memory block — memory is verbatim agent-authored content; we treat it as opaque text and skip the regex sub for that segment. Implementation: render substitution on `self_block + template` as today, then prepend the memory block AFTER substitution. (Substituting inside memory would break user content that happens to contain `{{...}}`.)

7. **No size cap** in v1 (matches triage decision; `MAX_INBOX_MESSAGES` exists for mail because mail is multi-message and can grow unboundedly — memory is a single curated index file the agent itself owns).

8. **Empty file handling**: if `MEMORY.md` exists but is empty/whitespace-only, skip the block (don't emit empty `<memory></memory>`). One `Path.read_text().strip()` check.

### Implementation sketch

```python
def _load_memory(agents_dir: Path, role: str, rig_path: Path | None) -> str:
    candidates: list[Path] = []
    if rig_path is not None:
        candidates.append(Path(rig_path) / ".claude" / "agents" / role / "memory" / "MEMORY.md")
    candidates.append(Path(agents_dir) / role / "memory" / "MEMORY.md")
    for p in candidates:
        if p.is_file():
            body = p.read_text()
            if body.strip():
                return f"<memory>\n{body.rstrip()}\n</memory>\n\n"
            return ""
    return ""
```

Call after substitution:

```python
rendered = re.sub(...)               # existing self+body substitution
memory_block = _load_memory(agents_dir, role, rig_path)
return memory_block + rendered
```

## Acceptance criteria (verbatim)

(1) render_template(agents_dir, role) checks for <agents_dir>/<role>/memory/MEMORY.md; if present, prepends content as <memory>...</memory> block; (2) backwards compat: roles without memory/ render unchanged; (3) pack-convention.md documents the new layout; (4) smoke: a role's prompt sees its own MEMORY.md content on second turn after writing to it on first

## Verification strategy

| AC | How verified |
|---|---|
| 1 | Unit test: write `agents/triager/memory/MEMORY.md` with known marker text, call `render_template`, assert the output starts with `<memory>\n...marker...\n</memory>` and is followed by the prompt body. |
| 2 | Unit test: existing tests (`test_renders_role_prompt`, `test_no_identity_renders_unchanged`) must keep passing byte-identically. Add explicit `test_no_memory_dir_renders_unchanged` that asserts no `<memory>` substring in output when only `prompt.md` exists. |
| 3 | `engdocs/pack-convention.md` updated with a new "Per-role memory" section describing layout, precedence (rig overlay wins, pack fallback), block ordering, no-cap policy, and that the agent owns reads/writes via plain file ops. |
| 4 | Two-turn smoke unit test (`test_smoke_second_turn_sees_first_turn_memory`): (a) call `render_template` once on a role with no memory dir → assert no `<memory>` in output; (b) write `MEMORY.md` to the same `agents/<role>/memory/` path; (c) call `render_template` again → assert the just-written content is in the output inside `<memory>...</memory>`. Simulates "agent writes memory on turn 1, sees it on turn 2" without spinning up a real Claude session. |

Manual sanity check (not gated): `uv run python -c "from prefect_orchestration.templates import render_template; ..."` against a tmpdir layout.

## Test plan

- **unit** (`tests/test_templates.py`):
  - `test_memory_block_prepended_when_present`
  - `test_no_memory_dir_renders_unchanged`
  - `test_empty_memory_file_renders_no_block`
  - `test_memory_block_precedes_self_block` — assert order `<memory>` then `<self>` then body when both identity.toml + MEMORY.md present.
  - `test_rig_overlay_memory_overrides_pack_memory`
  - `test_smoke_second_turn_sees_first_turn_memory` (AC4)
  - `test_memory_content_is_not_substituted` — file contains `{{notavar}}`, render does not raise KeyError.
- **e2e**: not needed — pure templating layer, no subprocess interaction. (Repo's `.po-env` skips e2e by default anyway.)
- **playwright**: N/A (no UI).

Existing baseline failures (27) are pre-existing and unrelated to this change; the regression-gate compares against baseline so no new failures may appear.

## Risks

- **API contract change**: `render_template` already returns a `str` and the new prefix is purely additive when the memory dir is absent (AC2). No signature change. Low risk.
- **Block-ordering regressions**: existing tests assert `out.startswith("<self>\n")` (`test_identity_self_block_prepended`). Once `<memory>` lands first, that assertion would break in the presence of memory — but those tests don't write a `memory/` dir, so `<self>` remains first there. Need to verify identity tests still pass; if any uses `startswith("<self>")` while also having memory, fix the assertion to `"<self>\n" in out` or assert ordered indices. Spot check during build.
- **AgentSession.prompt() interaction**: mail block is prepended at turn time and ends up *before* `<memory>`. Acceptable — mail is the most recent / time-sensitive context and belongs at the top. Document in pack-convention.md.
- **Memory block size**: no cap; a runaway memory file could blow the context window. Triage flagged this as acceptable for v1 (matches Claude Code, which also has no enforced cap). Future bead can add soft truncation if it becomes an issue.
- **No migration**: pure filesystem convention; existing roles need no changes.
- **No breaking consumers**: callers of `render_template` (in formula packs) do not need to change.
