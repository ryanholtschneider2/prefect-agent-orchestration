# Plan: prefect-orchestration-vtn — `po serve` pluggable PG creds

## Affected files

- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/prefect_orchestration/serve.py` — main change: parametrize unit templates, add CLI flags, creds-file plumbing, `--external-pg`, status/uninstall updates.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/CLAUDE.md` — update the `po serve install` section to document new flags + creds file location.
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/tests/test_serve.py` — new unit tests (mock `subprocess`, `systemctl`, `docker`; assert file contents + permissions + idempotency).
- `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration/README.md` (only if it currently documents `po serve` creds — quick grep check during build).

No changes needed in `cli.py` — `serve` is already a Typer sub-app.

## Approach

1. **Creds file** — introduce `CREDS_FILE = Path.home() / ".config" / "po" / "serve.env"` and a `ServeCreds` dataclass with `pg_user/pg_password/pg_db/pg_host/pg_port/external_url`. Helpers:
   - `load_creds() -> ServeCreds | None` — parses the env-file (KEY=VALUE) if present.
   - `save_creds(creds)` — writes `serve.env` with mode 0600, parent dir 0700. Format is systemd-`EnvironmentFile`-compatible (`KEY=value` per line, no quoting needed because `secrets.token_urlsafe` is URL-safe ASCII).
   - `build_db_url(creds) -> str` — assembles `postgresql+asyncpg://<user>:<urlquote(password)>@<host>:<port>/<db>`; if `external_url` set, return it unchanged.

2. **CLI flags on `install`** — add Typer options:
   - `--pg-user`, `--pg-password`, `--pg-db`, `--pg-host` (default `127.0.0.1`), `--pg-port` (default `5432`)
   - `--rotate-password` flag — regenerates a random password even if a creds file exists. Implementation: write the new password into the creds file before writing units. (Note: this works seamlessly only when the underlying PG volume is empty, e.g. fresh install or after `uninstall --purge-data`. If the data dir already exists with the old user, document that the user must `uninstall --purge-data` first; we will WARN — not silently fail — when `--rotate-password` is passed and `PG_DATA_DIR` exists with content, since `POSTGRES_*` env vars are only honored on first init by the postgres image.)
   - `--external-pg POSTGRESQL_URL` — when set, skip writing/enabling `prefect-postgres.service`, skip docker; only configure Prefect profile + run `prefect server database upgrade`. Mutually exclusive with the per-field flags (error if combined).

3. **Resolve creds order** (in `install`):
   1. If `--external-pg` → store `{external_url=...}` in creds file (still 0600), skip PG container path entirely.
   2. Else load existing creds file. Honor explicit `--pg-*` flag overrides (writes the new value back).
   3. If no file and no `--pg-password`, generate via `secrets.token_urlsafe(32)`.
   4. If no file and the legacy `prefect-postgres` container exists with the v1 hardcoded creds, write a creds file with `prefect/prefect/prefect` (backward-compat, mentioned in triage). Detect by inspecting `docker inspect prefect-postgres --format '{{range .Config.Env}}{{println .}}{{end}}'` for `POSTGRES_USER=prefect`. Best-effort; on any failure fall through to defaults+random password.
   5. Persist with `save_creds()`.

4. **Unit templates** — switch both PG and SERVER unit bodies to reference `EnvironmentFile=%h/.config/po/serve.env` and use shell-style `${POSTGRES_USER}` / `${POSTGRES_DB}` / `${PREFECT_API_DATABASE_CONNECTION_URL}` references (note: systemd `Environment=`/`EnvironmentFile=` exposes vars to `ExecStart` but only when the unit invokes a shell or the binary expands them; `docker run -e VAR` forwards from the unit's environment, so `-e POSTGRES_USER=${POSTGRES_USER}` works because docker reads from env. We will use the form `--env-file <(env)` style only if needed; simpler is `-e POSTGRES_USER -e POSTGRES_PASSWORD -e POSTGRES_DB` which docker interprets as "pass-through from current env"). Server unit's `ExecStartPre` `pg_isready` call is wrapped in `/bin/sh -c` so `${POSTGRES_USER}` and `${POSTGRES_DB}` expand. The `-p` mapping uses `${PG_HOST}:${PG_PORT}:5432`.

   The serve.env will contain: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `PG_HOST`, `PG_PORT`, `PREFECT_API_DATABASE_CONNECTION_URL`. (Last one is what the prefect-server unit needs; the first three are what docker forwards.)

5. **Profile sync (AC4)** — `prefect config set PREFECT_API_DATABASE_CONNECTION_URL=<build_db_url(creds)>` reads from the same `ServeCreds` instance just written; both sources are guaranteed identical because they're computed once.

6. **External-PG mode** — when `creds.external_url` is set:
   - Do not write `PG_UNIT`; if it exists from a prior local-PG install, leave it (uninstall handles removal). Don't enable it.
   - Server unit drops the `Requires=prefect-postgres.service` line and the `pg_isready` `ExecStartPre`.
   - `status()` shows `external` instead of running docker exec.

7. **`uninstall --purge-data` (AC5)** — after current cleanup, `CREDS_FILE.unlink(missing_ok=True)` and remove the `~/.config/po/` dir if empty. Without `--purge-data`, leave creds file in place.

8. **`status()`** — load creds; use `creds.pg_user`/`creds.pg_db` for `pg_isready -U $USER -d $DB`; for external-pg, print `(external)` and skip docker exec; falls back to legacy `prefect/prefect` only if no creds file (warn).

9. **Backward compat** — first run after upgrade where unit files exist but no creds file: generate creds file from the running container's env (or fallback to `prefect/prefect`) and rewrite units. Document in commit message.

10. **Docs (AC6)** — update `CLAUDE.md` § "One-time host setup (`po serve install`)":
   - List new flags
   - Mention `~/.config/po/serve.env` (0600)
   - Mention `--external-pg` mode
   - Update the manual one-liner to use a generated password
   - Note `--purge-data` removes creds file

## Acceptance criteria (verbatim)

(1) `po serve install` accepts `--pg-user`, `--pg-password`, `--pg-db`, `--pg-port`, `--pg-host` flags.
(2) Without flags, generate a random password on first install and store creds in a 0600 file (e.g. `~/.config/po/serve.env`) sourced by both systemd units.
(3) Re-running `po serve install` reuses the existing creds file (no rotation unless `--rotate-password`).
(4) `PREFECT_API_DATABASE_CONNECTION_URL` is set from the same source so the profile stays in sync.
(5) `po serve uninstall --purge-data` also removes the creds file.
(6) Docs in `CLAUDE.md` updated.
(7) Optional: `--external-pg postgresql://...` skips the docker container entirely and just configures Prefect to use a user-supplied PG instance.

## Verification strategy

| AC | How verified |
|---|---|
| 1 | Unit test: invoke `install` Typer command via `CliRunner` with each flag, mock `subprocess`/`systemctl`, assert flag values land in the creds file. |
| 2 | Unit test: HOME=tmp, no flags → assert creds file exists with mode `0o600`, parent dir `0o700`, password ≥ 32 chars and matches `[A-Za-z0-9_-]+`. Assert both unit files contain `EnvironmentFile=` pointing at the creds file. |
| 3 | Unit test: pre-write a creds file with known password; run `install` again with no flags; assert password unchanged. Then run with `--rotate-password`; assert password changed. |
| 4 | Unit test: capture `prefect config set` arg; assert URL contains the same user/password/host/port/db as the creds file (with URL-encoded password). |
| 5 | Unit test: pre-write creds file + data dir; run `uninstall --purge-data`; assert creds file gone. Run without `--purge-data`; assert creds file still present. |
| 6 | Manual review of `CLAUDE.md` diff during build; grep for new flag names. |
| 7 | Unit test: `install --external-pg postgresql://u:p@host:5/db`; assert PG unit not written, server unit lacks `Requires=prefect-postgres`, profile URL = the supplied URL, creds file has `external_url=...`. |

## Test plan

- **unit** (`tests/test_serve.py`) — primary coverage. Mock `shutil.which` to return fake `/usr/bin/{docker,prefect,systemctl}`; mock `subprocess.run`/`subprocess.call` to record args; use `monkeypatch` on `serve.UNIT_DIR`, `serve.PG_DATA_DIR`, `serve.CREDS_FILE` (or HOME) to point at `tmp_path`. Assert file contents, permissions, idempotency, flag plumbing.
- **e2e** — none added. The systemd + docker round-trip is intentionally outside CI scope (would require docker-in-docker + systemd-user session). Existing e2e suite is unaffected.
- **playwright** — N/A (no UI).

## Risks

- **Existing v1 installs**: users with hardcoded `prefect:prefect` creds in their Prefect profile + running PG container. Mitigation: backward-compat path detects via `docker inspect` and writes a creds file matching the running container before rewriting units. If detection fails we still write defaults (`prefect/prefect/prefect`) so existing installs keep working; only loss is no random password until `uninstall --purge-data && install`.
- **systemd `EnvironmentFile` quoting**: passwords with `$`, `#`, `\`, or `'` would break. Mitigation: `secrets.token_urlsafe` only emits `[A-Za-z0-9_-]`; for user-supplied `--pg-password` we will validate with the same charset and error out otherwise (documented in flag help). This avoids a quoting rabbit hole.
- **`--rotate-password` against existing data dir**: postgres image only honors `POSTGRES_PASSWORD` on first init; rotation against a populated volume silently no-ops. Mitigation: when `--rotate-password` and `PG_DATA_DIR` non-empty, emit a loud WARN with instructions ("`po serve uninstall --purge-data` first, or run `ALTER USER` manually"). Don't attempt automated `ALTER USER` — out of scope per triage.
- **`--external-pg` without `prefect server database upgrade` perms**: external PG might lack permissions to run upgrade. We surface the subprocess return code; document that the user must grant the role rights.
- **Concurrent `install` race**: last-writer-wins on the creds file. Acceptable per triage.
- **Breaking consumers**: none — `po serve` is end-user CLI; no library API changes. `cli.py`/`commands.py` untouched.

## Out of scope

- Automated `ALTER USER` password rotation against a live DB.
- Migrating an existing v1 install's data to a new password without downtime.
- Multi-host / remote PG TLS config (covered partly by `--external-pg`).
