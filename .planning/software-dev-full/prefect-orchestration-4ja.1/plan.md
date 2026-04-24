# Plan — prefect-orchestration-4ja.1: `po install / update / uninstall / packs`

## Affected files

- `prefect_orchestration/cli.py` — register four new Typer subcommands (`install`, `update`, `uninstall`, `packs`).
- `prefect_orchestration/packs.py` *(new)* — pack-lifecycle module:
  - `find_uv() -> str` (locate `uv` binary; raise `PackError` with install pointer if missing).
  - `discover_packs() -> list[PackInfo]` (scan `importlib.metadata.distributions()`, filter to dists exporting at least one of the four `po.*` EP groups).
  - `PackInfo` dataclass: `name`, `version`, `source` (PyPI / git / editable), `contributions: dict[str, list[str]]` keyed by EP-group name.
  - `install(spec, *, editable=False, force=True) -> None`, `update(name | None) -> None`, `uninstall(name) -> None`, `list_packs() -> list[PackInfo]`.
  - Use `uv tool install --upgrade prefect-orchestration --with <pack>` semantics (po lives in the same uv tool env, so packs are siblings, not separate tool envs). For `--editable`, append `--with-editable <path>`. For `update`, re-run the same install with `--reinstall` (or `--force --reinstall`) so entry-point metadata is rewritten.
  - Determine source via `Distribution.read_text("direct_url.json")` (PEP 610) — `dir_info.editable=True` → editable; `vcs_info` → git; else PyPI.
  - Refuse `po uninstall prefect-orchestration` with a guard message.
- `tests/test_packs.py` *(new)* — unit tests for discover/source-classification/argv-construction with `uv` mocked via `monkeypatch` on a `_run_uv` seam.
- `tests/test_cli_packs.py` *(new)* — Typer `CliRunner` tests for the four verbs (verify exit codes, error messages, argv passed to `_run_uv`).
- `README.md` — replace `uv add` / install instructions with `po install …`.
- `CLAUDE.md` (project) — replace both `uv tool install --force --editable …` blocks (lines 57 + 298–303) with `po install --editable …` / `po update` examples; drop the "Re-run uv tool install …" footgun warning (now solved by `po update`).

## Approach

Add a thin `packs.py` module that owns all `uv tool` shell-outs through a single `_run_uv(args: list[str]) -> subprocess.CompletedProcess` seam — easy to monkeypatch in tests. Public functions match the four verbs.

CLI surface in `cli.py`:

```
po install <spec>              # PyPI name, git URL, or path (auto-detect)
po install --editable <path>   # explicit editable path
po update [<pack>]             # one pack or all packs
po uninstall <pack>
po packs                       # table: name | version | source | contributes
```

Argument disambiguation for `po install <spec>` (no `--editable`):
1. If `spec` matches a git URL pattern (`git+`, `https://…/…\.git`, `git@`) → pass straight to uv as a git source.
2. Else if `Path(spec).exists()` and is a directory → treat as local path; install editable.
3. Else → treat as PyPI name.

`po packs` discovery: iterate `importlib.metadata.distributions()`; for each, collect EPs in groups `po.formulas`, `po.deployments`, `po.commands`, `po.doctor_checks`. Skip dists with no `po.*` EPs. Sort by name. Render as plain text table (no extra deps). Source classification via `direct_url.json`.

Error mapping: catch `subprocess.CalledProcessError`, prefix with `po install <pack> failed:` and show stderr. If `shutil.which("uv")` is None, print:
```
po requires the `uv` package manager.
Install: curl -LsSf https://astral.sh/uv/install.sh | sh
Then re-run: po <command>
```
and exit non-zero.

Guard: `po uninstall prefect-orchestration` → refuse with explanation that this would remove `po` itself; suggest manual `uv tool uninstall prefect-orchestration` if the user really means it.

## Acceptance criteria (verbatim from issue)

1. `po install <pack>` installs a pack via uv;
2. `po update` refreshes entry-point metadata for all installed packs;
3. `po packs` lists installed packs with what each contributes (grouped by entry-point group);
4. `po uninstall` removes cleanly;
5. README + CLAUDE.md updated — no more `uv tool install` in user docs;
6. principle §3 cited in PR body.

## Verification strategy

- **AC1**: unit test `test_install_invokes_uv_with_pack_spec` asserts `_run_uv` called with `["tool", "install", "--upgrade", "prefect-orchestration", "--with", "<pack>"]` (or equivalent). e2e test installs an editable test-pack fixture and asserts `po list` (or `po packs`) sees it.
- **AC2**: unit test `test_update_all_reinstalls_each_pack` mocks `discover_packs()` returning two packs, calls `po update`, asserts `_run_uv` called with `--reinstall` for each. Targeted test `test_update_named_pack` asserts `po update <name>` only re-installs that one.
- **AC3**: unit test fakes a Distribution with `po.formulas` + `po.commands` EPs, asserts `po packs` output table includes name, version, source, and grouped contributions. CliRunner test asserts column headers + contents.
- **AC4**: unit test `test_uninstall_invokes_uv_with_name` + guard test `test_uninstall_refuses_self` (asserts non-zero exit + helpful message when target is `prefect-orchestration`).
- **AC5**: grep-style test (or manual verification) — `grep -n "uv tool install" README.md CLAUDE.md` returns nothing user-facing (a contributor-section reference may remain if marked as developer-only). Plan: remove all instances, replacing with `po install`.
- **AC6**: documented for the human writing the PR body. Not code-verifiable; mention in `lessons-learned.md`.

## Test plan

- **Unit** (`tests/test_packs.py`, `tests/test_cli_packs.py`): cover argv construction, source classification, discover filter, error mapping, self-uninstall guard, missing-uv pointer. All use `monkeypatch` on `_run_uv` and `shutil.which` — no real subprocess calls.
- **E2E** (`tests/e2e/test_po_packs_cli.py` *optional*): one happy-path roundtrip — install a tiny in-tree fixture pack as editable, run `po packs`, assert it shows up; then `po uninstall` it. Gate on `uv` availability via `pytest.importorskip`/`shutil.which` skip.
- **Playwright**: N/A (no UI).
- Keep the two pre-existing baseline failures (`test_session_name_derivation`, `test_prompt_fragment_exists_and_mentions_inbox`) untouched — they're outside this issue's scope.

## Risks

- **uv invocation drift**: `uv tool install --upgrade <main-tool> --with <extra>` is the supported pattern for adding siblings to an existing tool env. If uv's flag names shift, the wrappers break — mitigated by single `_run_uv` seam and clear error mapping. Pin behavior with unit tests on argv.
- **EP-metadata refresh requires `--reinstall`**: must verify `--upgrade --reinstall` actually rewrites entry-point metadata for the *extra* (`--with`) packs, not just the main tool. If not, fall back to `uv tool install --force --reinstall prefect-orchestration --with <pack>` for the editable pack. Builder must spot-check during implementation.
- **Source classification via PEP 610**: `direct_url.json` is optional; PyPI installs may lack it. Treat absence as `pypi`. Editable installs always have `dir_info.editable=True`.
- **Self-uninstall guard**: must not block the user from removing po deliberately — surface the manual `uv tool uninstall` escape hatch.
- **No API contract break**: purely additive CLI verbs; no existing consumer affected. No migrations.
- **Doc churn**: removing `uv tool install` lines from CLAUDE.md may surprise contributors mid-flight. Keep one developer-only note in a "Contributing" section pointing at the underlying uv command for debugging, but drop it from user-facing flows.
