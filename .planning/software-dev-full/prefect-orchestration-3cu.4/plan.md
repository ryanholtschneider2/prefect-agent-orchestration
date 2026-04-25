# Plan — `prefect-orchestration-3cu.4` · `po-attio` tool pack

## Context

Build `po-attio`, a tool pack for the Attio CRM, following
`engdocs/pack-convention.md`. Attio ships no first-party CLI at the
time of writing, so the pack leads with the Python SDK and exposes
thin CLI wrappers as `po.commands`. Because `po-attio` maps to a
distinct external system (CRM), it is a **separate pack repo** — per
`pw4` and CLAUDE.md ("land pack-contrib code in the pack's repo, not
in the caller's rig-path").

Sibling tool pack convention is established by the existing
`po-formulas-software-dev` pack at
`/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/`.
We mirror its layout.

## Pack location

`/home/ryan-24/Desktop/Code/personal/nanocorps/po-attio/`

This is a sibling of `prefect-orchestration/` and mirrors the existing
`software-dev/po-formulas/` placement convention. The pack will be its
own git repo (`git init` at the pack root); the rig under which we
run is `prefect-orchestration/`, so all bd/PO artifacts stay there
while code lands at the sibling path.

## Affected files (all newly created under `po-attio/`)

```
po-attio/
├── pyproject.toml
├── README.md
├── .gitignore
├── po_attio/
│   ├── __init__.py
│   ├── client.py          # tiny SDK wrapper: load API key, build Attio client
│   ├── commands.py        # 3 command callables → po.commands
│   └── checks.py          # 2 doctor checks → po.doctor_checks
├── skills/
│   └── attio/
│       └── SKILL.md
└── overlay/
    ├── CLAUDE.md
    └── .env.example
```

No edits to `prefect-orchestration/` core.

## Approach

### `pyproject.toml`

- `name = "po-attio"`, version `0.1.0`, `requires-python = ">=3.11"`
- Dependency on the official Attio Python SDK. PyPI distribution is
  `attio` (verify at build time; if the canonical name turns out to
  be `attio-python`, swap accordingly with a `>=` floor that pins to
  a current release).
- `dependencies = ["attio>=0.1", "prefect-orchestration"]`
- Entry points:
  ```toml
  [project.entry-points."po.commands"]
  attio-find          = "po_attio.commands:find"
  attio-create-person = "po_attio.commands:create_person"
  attio-note          = "po_attio.commands:note"

  [project.entry-points."po.doctor_checks"]
  attio-env       = "po_attio.checks:env_set"
  attio-reachable = "po_attio.checks:workspace_reachable"
  ```
- Hatchling build backend with `packages = ["po_attio"]`.

### `po_attio/client.py`

One zero-arg `client()` helper that:

- Reads `ATTIO_API_KEY` from env (raise `RuntimeError` if unset — let
  the command CLI catch and exit non-zero with hint).
- Constructs the SDK client and returns it.
- Does NOT cache (commands are short-lived; cleaner to re-init).

### `po_attio/commands.py`

Three Typer-free plain callables matching the `po.commands` signature
contract used by `po_formulas/commands.py` (kwargs only; CLI dispatcher
in core does the parsing). Each accepts string kwargs and prints
human-friendly output to stdout. JSON output mode (`output_format`)
deferred — keep v1 small.

- `find(query: str, object_type: str = "people", limit: int = 10) -> None`
  - Calls SDK list/search for the requested object type
    (`people` | `companies`). Falls back to people if unrecognized.
  - Prints one-line-per-record with id, primary name, primary email
    (or domain for companies). Exits 2 on auth failure.
- `create_person(name: str, email: str | None = None, company: str | None = None) -> None`
  - Creates a person record. If `email` provided, attaches as primary
    email attribute; if `company` provided, attempts to look up the
    company by name and attach (best-effort — log a warning if not
    found, still creates the person).
  - Prints the new record id + Attio web URL on success.
- `note(target_id: str, body: str, title: str | None = None,
       parent_object: str = "people") -> None`
  - Creates a note attached to `target_id` (a person or company
    record id). `body` is the note content (Markdown allowed). If
    `body == "-"`, read from stdin (lets agents pipe long content).
  - Prints note id on success.

All three:
- Wrap SDK exceptions; on auth failures print a clear message and
  `raise SystemExit(2)` (mirrors `summarize_verdicts` exit-code
  convention in the software-dev pack).
- Accept the standard `--key value` / `--key=value` flag forms — core
  argparse handles coercion (int, bool) automatically.

### `po_attio/checks.py`

Two `DoctorCheck` callables:

- `env_set()` — reads `ATTIO_API_KEY`. Returns
  - `red` if unset, hint: `export ATTIO_API_KEY` from your vault.
  - `green` with `f"ATTIO_API_KEY set ({key[:8]}…)"` otherwise.
  No format validation (Attio doesn't publish a stable token prefix).
- `workspace_reachable()` — short-circuits to `yellow` if
  `ATTIO_API_KEY` is unset (so the row is informative even pre-key);
  otherwise calls a cheap SDK endpoint (e.g. `client.workspaces.me()`
  or list-objects with `limit=1`) inside a `try/except` with a
  4-second timeout. `green` on success with the workspace name in the
  message; `red` with the upstream error string on failure.

Both follow the `claude_cli_present` template in
`po_formulas/checks.py`.

### `skills/attio/SKILL.md`

Standard Claude Code skill:

- YAML frontmatter (`name: attio`, one-line `description`).
- "Canonical vendor docs" section — links:
  - Attio API ref: https://developers.attio.com/reference
  - Attio docs root: https://developers.attio.com
  - Note that Attio publishes no `llms.txt` at time of writing.
- "SDK first (no vendor CLI)" callout explaining the inversion of the
  usual CLI-first preference and pointing agents at the
  `po attio-*` commands as the day-to-day surface.
- "This nanocorp's rules" — placeholder for tenant-specific policy
  (which workspace, project-list discipline, do-not-touch list-types).
- "Quick recipes" — two small SDK code blocks (find a person, attach
  a note) plus the matching `po attio-*` command lines.

### `overlay/CLAUDE.md`

Pack-specific agent overlay (copied into rig cwd at session start by
the overlay machinery from `4ja.4`). Repeats the headline rules from
`SKILL.md`:

- "Use `po attio-find`, `po attio-create-person`, `po attio-note`
  rather than constructing HTTP calls."
- "Set `ATTIO_API_KEY` before invoking; check with `po doctor`."

### `overlay/.env.example`

Single line: `ATTIO_API_KEY=`

### `README.md`

Short human-facing readme: what the pack is, install one-liner
(`po install --editable /path/to/po-attio`), pointer to SKILL.md.

### `.gitignore`

Standard Python gitignore (`__pycache__/`, `*.egg-info/`, `dist/`,
`.venv/`).

## Acceptance criteria (verbatim from issue)

1. dep: attio client lib
2. `skills/attio/SKILL.md` with doc links + note that SDK is primary because vendor lacks CLI
3. 3 commands
4. 2 doctor checks
5. overlay

## Verification strategy

| AC | How verified |
|---|---|
| 1 | `grep '"attio' po-attio/pyproject.toml` shows the dep; `cat pyproject.toml` confirms it sits in `[project] dependencies`. |
| 2 | File exists at `po-attio/skills/attio/SKILL.md`; `grep -i 'developers.attio.com' SKILL.md` returns hits; `grep -i 'sdk' SKILL.md` returns the "SDK is primary because Attio ships no CLI" sentence. |
| 3 | `pyproject.toml` lists `attio-find`, `attio-create-person`, `attio-note` under `[project.entry-points."po.commands"]`; each maps to a callable defined in `po_attio/commands.py`; `python -c "from po_attio.commands import find, create_person, note"` succeeds. |
| 4 | `pyproject.toml` lists two entries under `[project.entry-points."po.doctor_checks"]`; both callables return `DoctorCheck` instances when imported and called (verified via a smoke run with `ATTIO_API_KEY` unset → `env_set` returns red, `workspace_reachable` returns yellow). |
| 5 | `po-attio/overlay/CLAUDE.md` and `po-attio/overlay/.env.example` exist. |

End-to-end smoke (manual, post-install):

```bash
po install --editable /home/ryan-24/Desktop/Code/personal/nanocorps/po-attio
po update
po packs                              # po-attio listed
po list                               # attio-find / attio-create-person / attio-note shown
po show attio-find                    # signature + docstring
po doctor                             # 2 attio-* rows present (red/yellow if no key)
```

## Test plan

This is a small tool pack; full unit-test coverage is overkill but a
minimal smoke layer prevents regressions.

- **Unit tests** (`po-attio/tests/test_smoke.py`):
  - Import each command callable and each check callable.
  - With `ATTIO_API_KEY` unset, `env_set()` returns `status == "red"`.
  - With `ATTIO_API_KEY` unset, `workspace_reachable()` returns
    `status == "yellow"` (no SDK call attempted).
  - Confirm `pyproject.toml` entry points resolve via
    `importlib.metadata.entry_points(group="po.commands")` and
    `group="po.doctor_checks"` after editable install.
- **Live SDK calls** are NOT tested — they require a real key and
  network. Tests mock or skip; live verification is a manual smoke
  step the human runs once.
- **Playwright / e2e**: not applicable (no UI, no flow registered).

## Risks

- **SDK package name uncertainty.** PyPI publishes `attio` (Attio's
  official SDK as of 2025). If that resolves but is unmaintained, fall
  back to direct `httpx` calls against `https://api.attio.com/v2/`.
  Builder verifies via `pip index versions attio` before committing.
- **API surface drift.** Attio's API is in active development; any
  endpoint signature we hit (search, create, notes) may change. We
  thinly wrap the SDK; signature drift surfaces as upstream-SDK
  exception messages, which the commands print verbatim. No retries,
  no clever recovery.
- **Workspace-reachable check cost.** Calling Attio on every `po
  doctor` adds latency. We bound it to 4s and run inside the existing
  5s soft-timeout the core wraps each check in. If even 4s is too
  long, we degrade to env-only and remove the live probe — this is a
  policy call we'll revisit if `po doctor` slows perceptibly.
- **No git remote on `po-attio` yet.** The pack is local-only;
  `po install --editable <path>` covers the dev install. PyPI
  publishing is a follow-up issue.
- **No breaking consumer impact.** The pack is additive;
  no core code changes; existing rigs without `po-attio` installed
  see no behavior change.
- **Overlay collision.** `overlay/CLAUDE.md` lands in rig cwd at
  session start. The overlay machinery is skip-existing, so it does
  not clobber a user-authored `CLAUDE.md`. No risk of stomping on
  the rig's existing top-level `CLAUDE.md`.
