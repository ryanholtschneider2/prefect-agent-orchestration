# Plan — prefect-orchestration-3cu.4 (po-attio tool pack)

## Context

Build a new PO pack `po-attio` — a CRM tool pack for Attio. Lands as a
sibling repo per principle `pw4` (pack-contrib code lives in its own
repo, not in the rig). The rig only carries planning/decision-log
artifacts for this issue.

Sibling pack location: `/home/ryan-24/Desktop/Code/personal/nanocorps/po-attio/`

A prior build iteration already scaffolded the pack at that path; this
plan documents the intended shape and lets the critic / verifier
validate against it.

## Affected files

### Sibling pack (`/home/ryan-24/Desktop/Code/personal/nanocorps/po-attio/`)

```
pyproject.toml                       # name=po-attio, dep on attio>=0.21,
                                     # entry points: po.commands (3),
                                     # po.doctor_checks (2)
README.md                            # short usage blurb
.gitignore
po_attio/__init__.py
po_attio/client.py                   # ENV_VAR, _api_key(), client() — lazy
                                     # imports `from attio import SDK`,
                                     # returns SDK(oauth2=key)
po_attio/commands.py                 # find / create_person / note callables
po_attio/checks.py                   # env_set / workspace_reachable
                                     # DoctorCheck factories
skills/attio/SKILL.md                # YAML frontmatter + canonical doc
                                     # links + "SDK is primary because
                                     # vendor lacks CLI" rationale +
                                     # command summary table + recipe
overlay/CLAUDE.md                    # short rules for agents using
                                     # the pack inside an overlayed cwd
overlay/.env.example                 # ATTIO_API_KEY=
tests/__init__.py
tests/test_smoke.py                  # 6 offline tests (env-set paths,
                                     # entry-point declarations, dep)
```

### Rig (`prefect-orchestration`)

- `.planning/software-dev-full/prefect-orchestration-3cu.4/plan.md` — this file
- `.planning/software-dev-full/prefect-orchestration-3cu.4/decision-log.md`
- `.planning/software-dev-full/prefect-orchestration-3cu.4/build-iter-N.diff`

No core changes. `prefect-orchestration` already supports
`po.commands` / `po.doctor_checks` / `skills/` / `overlay/` discovery —
this pack is purely additive.

## Approach

1. **Pack location** (resolves triage open-question): sibling repo per
   `pw4`. Initialize as a standalone git repo so it can later get its
   own remote / lifecycle independent of the rig.

2. **SDK choice** (resolves "Attio Python SDK" open question): the
   `attio` PyPI package (Speakeasy-generated from the OpenAPI spec —
   verified `attio==0.21.2` exists; `attio-python` does NOT exist on
   PyPI despite being referenced in the issue design). Pin
   `attio>=0.21`. Auth surface: `SDK(oauth2=<api_key>)`.

3. **Client helper** (`po_attio/client.py`): `ENV_VAR = "ATTIO_API_KEY"`,
   `MissingApiKey(RuntimeError)`, `_api_key()` reads + validates,
   `client()` lazy-imports `from attio import SDK` so checks/imports
   stay cheap when the SDK isn't installed yet.

4. **Three commands** (`po_attio/commands.py`, AC #3):
   - `attio-find --query <q> [--object-type people|companies] [--limit N]`
     — calls `sdk.records.post_v2_objects_target_records_query(...)`
     with a `$contains` filter; defaults to `people`; falls back to
     people on unknown object_type. Prints `id  name  email/domain`.
   - `attio-create-person --name <n> [--email <e>] [--company <c>]`
     — builds a values dict (always `name`; conditionally
     `email_addresses`, `company` lookup); calls
     `sdk.records.post_v2_objects_target_records(target="people", ...)`;
     prints `created person <id>  https://app.attio.com/_/people/<id>`.
   - `attio-note --target-id <id> --body <b> [--title <t>] [--parent-object people|companies]`
     — `body == "-"` reads stdin; defaults `title` to first 80 chars of
     body; calls `sdk.notes.post_v2_notes(...)` in markdown format.

   All three: `MissingApiKey → SystemExit(2)`; other SDK errors →
   `SystemExit(1)` with stderr explanation. Defensive helpers
   (`_record_id`, `_attr_first`) tolerate Pydantic-or-dict shapes.

5. **Two doctor checks** (`po_attio/checks.py`, AC #4):
   - `env_set()` — red if `ATTIO_API_KEY` unset, green otherwise (with
     8-char truncated preview in message; never the full key).
   - `workspace_reachable()` — yellow short-circuit if env unset (avoid
     cascading red); on import-error → red with hint to `po install`;
     calls `sdk.objects.get_v2_objects()` (cheap, idempotent metadata
     endpoint), returns green w/ object count or red w/ upstream error.

6. **Skill** (`skills/attio/SKILL.md`, AC #2): YAML frontmatter
   `name: attio`; sections: canonical doc URLs
   (developers.attio.com, REST reference, OpenAPI spec); explicit
   "SDK is primary because Attio ships no CLI" note (verbatim per AC);
   command summary table; SDK fallback recipe with
   `from attio import SDK` example; nanocorp-specific rules
   (one workspace, append-only notes, dedupe via find-before-create).

7. **Overlay** (`overlay/`, AC #5):
   - `overlay/CLAUDE.md` — short rules: prefer `po attio-*`, set
     `ATTIO_API_KEY`, read `.claude/skills/po-attio/attio/SKILL.md`,
     use `--body=-` for long content.
   - `overlay/.env.example` — single line `ATTIO_API_KEY=`.

   Overlay merge is skip-existing per pack-convention.md, so this
   never clobbers caller files.

8. **Dependency** (`pyproject.toml`, AC #1): `attio>=0.21`.

9. **Tests** (`tests/test_smoke.py`): all offline — no live API calls,
   no SDK network mocks. Six tests:
   - imports resolve (`po_attio.client`, `po_attio.commands`, `po_attio.checks`)
   - `env_set()` red when env unset
   - `env_set()` green when env set, with truncated key preview
   - `workspace_reachable()` yellow when env unset (short-circuit branch)
   - `pyproject.toml` declares the exact 3 commands + 2 doctor checks
     under the right entry-point groups, pointing to real module paths
   - `pyproject.toml` declares dep on `attio` SDK (`>=0.21`)

## Acceptance criteria (verbatim from issue)

> (1) dep: attio client lib; (2) skills/attio/SKILL.md with doc links + note that SDK is primary because vendor lacks CLI; (3) 3 commands; (4) 2 doctor checks; (5) overlay.

## Verification strategy

| AC | Concrete check |
|----|----------------|
| (1) attio client lib dep | `python -c "import tomllib; assert any(d.startswith('attio') for d in tomllib.load(open('pyproject.toml','rb'))['project']['dependencies'])"` + smoke test `test_dependency_on_attio_sdk` |
| (2) skill doc | `test -f skills/attio/SKILL.md && grep -q 'developers.attio.com' skills/attio/SKILL.md && grep -qi 'SDK is primary' skills/attio/SKILL.md` |
| (3) 3 commands | `pip install -e . && po update && po list \| grep -E '^attio-(find\|create-person\|note)' \| wc -l` returns 3; smoke test `test_pyproject_declares_entry_points` asserts the exact key set under `po.commands` |
| (4) 2 doctor checks | `po doctor` shows two `attio-*` rows under the SOURCE column; smoke test asserts the exact key set under `po.doctor_checks` |
| (5) overlay | `test -f overlay/CLAUDE.md && test -f overlay/.env.example` |

## Test plan

- **Unit** (`tests/test_smoke.py` in the sibling pack): the 6 tests
  above. Run via `uv run python -m pytest tests/` from inside the
  pack repo.
- **E2E**: not added — the rig's e2e suite covers core PO flows; this
  pack is purely additive and offline-testable. A manual smoke (set
  `ATTIO_API_KEY`, run each command against a sandbox workspace) is
  in scope for the verifier but not codified as automated e2e.
- **Playwright**: N/A (no UI).

## Risks

- **Attio SDK package name** — issue design said `attio-python`, but
  PyPI inspection shows `attio` is the real distribution. Pinning the
  wrong name would break `po install`. *Mitigation*: verified at
  build-time via `pip index versions attio` and a smoke test asserting
  the dep string.
- **SDK API drift** — Speakeasy-generated SDKs use awkward, version-
  coupled method names (`post_v2_objects_target_records_query`).
  A minor SDK bump could rename methods. *Mitigation*: pin
  `attio>=0.21` (compat range) and rely on smoke tests + doctor's
  `workspace_reachable` to surface drift fast; defensive shape access
  in `_record_id`/`_attr_first` tolerates Pydantic↔dict drift.
- **`po doctor` latency** — `workspace_reachable` makes a real
  network call. *Mitigation*: core wraps each pack check in a 5-second
  soft timeout already (`prefect_orchestration.doctor`), so a slow
  Attio response degrades to yellow rather than hanging the table.
- **No git remote on rig** — finishing requires only local commits per
  rig CLAUDE.md; pack repo is also local-only for now.
- **Breaking consumers** — none. Pack is purely additive: new entry
  points, new commands, no edits to core or other packs.
- **Overlay collision** — overlay merge is skip-existing in core, so
  `overlay/CLAUDE.md` never overwrites a caller's existing CLAUDE.md.
