# Plan: prefect-orchestration-4ja.4 — overlay/ + skills/ copied into rig at session start

## Affected files

- `prefect_orchestration/pack_overlay.py` — **new**. Pack discovery + overlay/skills materialization. Stand-alone module so it's testable without pulling in the Claude CLI.
- `prefect_orchestration/agent_session.py` — `AgentSession` gains `overlay: bool = True` and `skills: bool = True` fields; first `prompt()` call (or `__post_init__`) triggers a one-shot materialization keyed off `(repo_path, role)`. Per-role overlay stacking lives here (uses `self.role`).
- `engdocs/pack-convention.md` — flesh out the "Overlay" + "Skills" sections with the actual semantics this plan implements (skip-existing for overlay, always-overwrite for skills, per-role stacking precedence, opt-out flags). Currently those sections are forward-looking ("see 4ja.4"); this issue makes them concrete.
- `tests/test_pack_overlay.py` — **new**. Unit tests for pack discovery + materialization (skip-existing, skill overwrite, per-role stacking, opt-out, executable bit, no-op when pack has neither dir).
- `tests/test_agent_session_overlay.py` — **new** (or fold into existing `tests/test_agent_session_tmux.py`). Verifies AgentSession invokes overlay materialization once with the right inputs and honors `overlay=False`/`skills=False`.
- `tests/_fixtures.py` — small helper to install a fake pack on disk + register it via a stub `entry_points` callable, so tests don't need real `pip install`.

## Approach

### Discovery (in `pack_overlay.py`)

Discover packs by walking `importlib.metadata` entry-point **distributions** that publish to any of our PO groups (`po.formulas`, `po.commands`, `po.doctor_checks`, `po.deployments`). For each distribution, look up its top-level package directory by importing the entry-point's loaded module and walking `Path(module.__file__).parent` up until the dir name matches the dist's top-level package, then take its **parent** — that's the repo/source root which contains `overlay/` and `skills/` per the pack convention (sibling to `po_<module>/`).

Return a list of `Pack(name=str, root=Path)`. Cache by distribution name to avoid repeated import work.

(`name` is the distribution name with the leading `po-` preserved — used as the skills subfolder per AC 8.)

### Materialization

Two functions, both idempotent:

```python
def apply_overlay(pack: Pack, cwd: Path, *, role: str | None = None) -> list[Path]:
    # Pack-wide overlay first, then per-role overlay (stacks on top — same skip-existing rule).
    # Walk pack.root/"overlay" and pack.root/f"po_*/agents/{role}/overlay" (if role given).
    # For each src file: if dest exists, skip; else copy preserving mode (shutil.copy2 + chmod fallback).
    # Returns list of files written (for telemetry/tests).

def apply_skills(pack: Pack, rig_path: Path) -> list[Path]:
    # For each pack.root/"skills/<skill-name>/SKILL.md" + sibling files:
    #   copy tree to rig_path/.claude/skills/<pack.name>/<skill-name>/, OVERWRITING.
    # Use shutil.copytree(..., dirs_exist_ok=True) plus an explicit per-file overwrite.
    # Returns list of files written.
```

Top-level driver:

```python
def materialize_packs(cwd: Path, *, role: str | None,
                      overlay: bool = True, skills: bool = True,
                      packs: Sequence[Pack] | None = None) -> None:
    for pack in packs if packs is not None else discover_packs():
        if overlay:
            apply_overlay(pack, cwd, role=role)
        if skills:
            apply_skills(pack, rig_path=cwd)
```

`cwd` is the rig path (the agent's working directory == rig). `.claude/skills/<pack-name>/` lives inside that.

### AgentSession integration

- Add fields: `overlay: bool = True`, `skills: bool = True`.
- Add a private `_materialized: bool = field(default=False, init=False, repr=False)` guard.
- In `prompt()` (before delegating to backend), if not yet materialized, call `materialize_packs(self.repo_path, role=self.role, overlay=self.overlay, skills=self.skills)` and set the guard. Doing it lazily in `prompt` (not `__post_init__`) keeps construction cheap and lets the `RoleRegistry` build sessions during tests without hitting the filesystem.
- Pack-wide overlay is rig-scoped; multiple roles in the same rig will all attempt to copy, but skip-existing makes subsequent roles' calls a no-op for shared files. Per-role overlay still applies cleanly per role (different source dirs, same skip-existing rule).

### Per-role overlay precedence (resolves triage open question)

1. Files already on disk in `cwd` win — never overwritten (filesystem presence, not git status; simpler and matches AC 3).
2. Per-role overlay (`po_<mod>/agents/<role>/overlay/**`) processed before pack-wide; if it lays down a file, the pack-wide overlay then skips it (because it now "exists"). This is the cleanest way to express "role overrides pack-wide".
3. Pack-wide overlay (`<pack>/overlay/**`) fills in remaining files.

Order documented in `engdocs/pack-convention.md`.

### Skills semantics

- Always overwrite. Pack ships canonical content; agent edits to `.claude/skills/<pack-name>/` are explicitly ephemeral.
- Destination layout: `<rig>/.claude/skills/<pack-name>/<skill-name>/SKILL.md` (+ any sibling files like images/scripts the skill ships).
- We don't touch `.claude/skills/` subdirs that don't match an installed pack name — leaves user-authored skills alone.

### Symlinks / executable bits

Use `shutil.copy2` (preserves mode + mtime). For directories, walk manually or use `shutil.copytree(..., dirs_exist_ok=True, copy_function=shutil.copy2)`. Symlinks: copy as regular files (`follow_symlinks=True`) — packs shouldn't ship symlinks, but if they do we materialize the target rather than re-pointing into the install dir.

## Acceptance criteria (verbatim)

> (1) `<pack>/overlay/**` copied into session cwd at start; (2) `<pack>/skills/<name>/SKILL.md` copied into `<rig>/.claude/skills/<pack-name>/<name>/SKILL.md`; (3) existing files in cwd not overwritten by overlay (skip-existing); (4) skills always overwritten from pack (canonical); (5) per-role overlay stacks on pack-wide; (6) opt-out via `AgentSession(overlay=False, skills=False)`; (7) documented in `pack-convention.md`; (8) tested with `po-stripe` reference pack (`skills/stripe/SKILL.md` delivers to `<rig>/.claude/skills/po-stripe/stripe/SKILL.md`).

## Verification strategy

| AC | How verified |
|---|---|
| 1 | Unit test: install fake pack with `overlay/CLAUDE.md` + `overlay/scripts/run.sh`; call `materialize_packs(tmp_cwd)`; assert both files present at expected paths. |
| 2 | Unit test: fake pack with `skills/foo/SKILL.md`; assert it lands at `tmp_cwd/.claude/skills/<pack-name>/foo/SKILL.md`. |
| 3 | Unit test: pre-create `tmp_cwd/CLAUDE.md` with custom content; run materialize; assert content unchanged after pack overlay copies. |
| 4 | Unit test: pre-create `tmp_cwd/.claude/skills/<pack-name>/foo/SKILL.md` with stale content; run materialize; assert content matches pack source (overwritten). |
| 5 | Unit test: fake pack with `overlay/CLAUDE.md` + `po_pkg/agents/builder/overlay/CLAUDE.md`; call with `role="builder"`; assert builder version wins. Re-run with `role="critic"`; assert pack-wide version is laid down for that rig path (or stays from first run — both fine, document). |
| 6 | Unit test: monkeypatch `materialize_packs` to record calls; instantiate `AgentSession(overlay=False, skills=False)` with stub backend; call `prompt`; assert `materialize_packs` invoked with `overlay=False, skills=False` (or, simpler: assert no files appear in tmp cwd). |
| 7 | Doc check: section titled "Overlay" and "Skills" in `engdocs/pack-convention.md` describes skip-existing, overwrite, per-role stacking, opt-out — manual review by critic. |
| 8 | Either (a) integration test that spins up a fake `po-stripe` pack via the same fixture and verifies the literal target path from AC 8, or (b) defer to the `po-stripe` reference-pack issue (`hmc`) and add a fixture-based equivalent here. Plan: do (a) — name the fixture pack `po-stripe` so the path AC 8 names is exercised verbatim. |

## Test plan

- **Unit** (primary): `tests/test_pack_overlay.py` covers all functional cases (1–6 above) using a `_fake_pack` fixture that writes to tmpdir and patches `entry_points`. `tests/test_agent_session_overlay.py` (or extension of existing tmux test file) covers the `AgentSession` wiring.
- **E2E**: not needed. The `po run` flow doesn't gain new CLI surface; existing e2e tests will exercise overlay incidentally once the pack ships overlay content. We will not add a new e2e file.
- **Playwright**: N/A (no UI).

## Risks

- **Pack-root discovery**: walking up from `module.__file__` to find the source root assumes a flat layout (`<pack>/po_<mod>/` sibling to `<pack>/overlay/`). For wheels installed from PyPI the package usually lives in `site-packages/po_<mod>/` with `overlay/` shipped inside the wheel as `site-packages/<pack-name>-<ver>.dist-info/...` — overlay would NOT survive as a sibling dir. **Mitigation**: when constructing the wheel, packs must include `overlay/` and `skills/` via `[tool.hatch.build.targets.wheel] include = ["overlay/**", "skills/**"]` *or* place them inside the importable package (e.g. `po_<mod>/overlay/`). Plan: support **both** layouts — discovery probes `<dist-root>/overlay/` first, then `<package-root>/overlay/`. Document in pack-convention.md. Editable installs (the dev workflow we use) hit the first path; non-editable installs hit the second.
- **Concurrent sessions writing the same overlay**: skip-existing is racy (TOCTOU). Two roles starting simultaneously could both decide a file doesn't exist and both write it. Race results are identical bytes; downside is wasted IO, not corruption. Acceptable for v1; revisit if it shows up in logs.
- **Mode preservation across filesystems**: `shutil.copy2` preserves mode on POSIX; on Windows or some FUSE mounts the `chmod` may no-op. Out of scope — PO is Linux-first.
- **Surprising materialization on every `prompt()` call**: guarded by `_materialized` per-session, but two `AgentSession`s in the same `RoleRegistry` will each materialize. With skip-existing this is cheap (stat per file) — acceptable.
- **API contract**: adding two boolean fields with safe defaults to `AgentSession` is non-breaking. No callers need updating; opt-out is opt-in.
- **No migrations / no schema / no breaking consumers.**
