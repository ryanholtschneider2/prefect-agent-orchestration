# Decision log — prefect-orchestration-4ja.1 (builder iter 1)

- **Decision**: Implement all four verbs as a single `uv tool install --reinstall prefect-orchestration --with[-editable] <spec>` call rather than per-pack tool envs.
  **Why**: Principle §3 + triage §18: `po` itself lives in a uv tool env and packs must share that env so `importlib.metadata` sees their entry points. `uv` has no per-extra add/remove primitive, so uninstall also re-invokes the aggregate install with the target removed.
  **Alternatives considered**: (a) separate `uv tool install` per pack (breaks EP discovery — different venvs); (b) `uv pip install` into core's venv (would drift from `uv tool` state).

- **Decision**: `--reinstall` flag (not `--force`) for install/update/uninstall.
  **Why**: Modern uv uses `--reinstall` to rewrite entry-point metadata; `--force` is an alias/legacy. Keeping a single flag keeps argv tests stable and matches the risk section of the plan.
  **Alternatives considered**: `--force --reinstall` (belt-and-braces); dropped as redundant.

- **Decision**: Source classification via PEP 610 `direct_url.json`; absence ⇒ `pypi`.
  **Why**: It's the standard way to distinguish editable / git / local installs post-install. Plan §Approach.
  **Alternatives considered**: Parsing `RECORD` / sysconfig paths — fragile and uv-version-dependent.

- **Decision**: `po install <spec>` auto-detects a local directory (upgrades to editable) but does NOT require `--editable` for git URLs.
  **Why**: Matches the plan + uv's own spec grammar (`uv tool install ./path` installs from path, git+ URLs are handled natively).
  **Alternatives considered**: Require explicit `--editable` for paths — rejected as boilerplate.

- **Decision**: Skipped mcp-agent-mail file reservations.
  **Why**: Project wasn't registered in the mail server and the `register_agent` API enforces adjective+noun names, refusing role-style `issue:role` identifiers. Advisory-only; risk is bounded (no concurrent workers on this issue).
  **Alternatives considered**: Auto-generate an agent name, then reserve — deferred as yak-shaving for iter 1.

- **Decision**: `tests/test_cli_packs.py` uses `typer.testing.CliRunner`, not subprocess.
  **Why**: Faster, no `po` binary needed, plays nicely with `monkeypatch.setattr(packs, "_run_uv", ...)`. E2E against real `uv` is out-of-scope for iter 1 (plan marks it optional).
  **Alternatives considered**: real subprocess e2e — deferred until CI gates on `uv` availability.
