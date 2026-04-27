"""`po serve` — install/uninstall systemd-user units that run a Postgres
container + Prefect server in the background, default backend for `po run`.

Postgres lives in `~/.local/share/prefect-postgres/` (bind mount). Prefect
server listens on 127.0.0.1:4200. Both units are user-scoped (`systemctl
--user`) so no root needed; `loginctl enable-linger $USER` keeps them
running after logout.

Idempotent: re-running `install` rewrites the unit files and reloads.

Credentials live in `~/.config/po/serve.env` (mode 0600), sourced by both
units via `EnvironmentFile=`. On first install a random PG password is
generated; subsequent installs reuse the file unless `--rotate-password`
is passed. `--external-pg postgresql://...` skips the local container
entirely and just configures Prefect to use a user-supplied PG instance.
"""

from __future__ import annotations

import re
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

import typer

UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
PG_UNIT = UNIT_DIR / "prefect-postgres.service"
SERVER_UNIT = UNIT_DIR / "prefect-server.service"
PG_DATA_DIR = Path.home() / ".local" / "share" / "prefect-postgres"
CREDS_DIR = Path.home() / ".config" / "po"
CREDS_FILE = CREDS_DIR / "serve.env"

# systemd EnvironmentFile is finicky about quoting; we restrict creds to a
# safe charset (URL-safe base64 + a few separators) so values can be written
# bare. token_urlsafe already satisfies this; we apply the same rule to
# user-supplied values to avoid a quoting rabbit-hole.
_SAFE_CRED_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


@dataclass
class ServeCreds:
    pg_user: str = "prefect"
    pg_password: str = ""
    pg_db: str = "prefect"
    pg_host: str = "127.0.0.1"
    pg_port: str = "5432"
    external_url: str = ""

    def is_external(self) -> bool:
        return bool(self.external_url)


PG_UNIT_TEMPLATE = """\
[Unit]
Description=Postgres for Prefect (po backend)
After=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
EnvironmentFile={creds_file}
ExecStartPre=-/usr/bin/docker rm -f prefect-postgres
ExecStart=/usr/bin/docker run -d --name prefect-postgres \\
    --restart=no \\
    -e POSTGRES_USER \\
    -e POSTGRES_PASSWORD \\
    -e POSTGRES_DB \\
    -p ${{PG_HOST}}:${{PG_PORT}}:5432 \\
    -v %h/.local/share/prefect-postgres:/var/lib/postgresql/data \\
    postgres:16-alpine
ExecStop=/usr/bin/docker stop prefect-postgres

[Install]
WantedBy=default.target
"""

SERVER_UNIT_TEMPLATE_LOCAL = """\
[Unit]
Description=Prefect Server (UI + API on :4200)
After=prefect-postgres.service network-online.target
Requires=prefect-postgres.service

[Service]
Type=simple
EnvironmentFile={creds_file}
Environment=PREFECT_HOME=%h/.prefect
ExecStartPre=/bin/sh -c 'until /usr/bin/docker exec prefect-postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; do sleep 1; done'
ExecStart={prefect_bin} server start --host 127.0.0.1 --port 4200
Restart=on-failure
RestartSec=5
StandardOutput=append:%h/.prefect/server.log
StandardError=append:%h/.prefect/server.log

[Install]
WantedBy=default.target
"""

SERVER_UNIT_TEMPLATE_EXTERNAL = """\
[Unit]
Description=Prefect Server (UI + API on :4200, external Postgres)
After=network-online.target

[Service]
Type=simple
EnvironmentFile={creds_file}
Environment=PREFECT_HOME=%h/.prefect
ExecStart={prefect_bin} server start --host 127.0.0.1 --port 4200
Restart=on-failure
RestartSec=5
StandardOutput=append:%h/.prefect/server.log
StandardError=append:%h/.prefect/server.log

[Install]
WantedBy=default.target
"""

app = typer.Typer(
    help="Install/manage the Postgres + Prefect server background stack.",
    no_args_is_help=True,
)


# ---- creds plumbing ---------------------------------------------------------


def _validate_safe(field_name: str, value: str) -> None:
    if not _SAFE_CRED_RE.match(value):
        raise typer.BadParameter(
            f"{field_name}={value!r}: must match {_SAFE_CRED_RE.pattern} "
            "(systemd EnvironmentFile values are written bare, no quoting)."
        )


def load_creds() -> ServeCreds | None:
    """Parse ~/.config/po/serve.env if present. Returns None if missing."""
    if not CREDS_FILE.exists():
        return None
    data: dict[str, str] = {}
    for line in CREDS_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        data[key.strip()] = val.strip()
    return ServeCreds(
        pg_user=data.get("POSTGRES_USER", "prefect"),
        pg_password=data.get("POSTGRES_PASSWORD", ""),
        pg_db=data.get("POSTGRES_DB", "prefect"),
        pg_host=data.get("PG_HOST", "127.0.0.1"),
        pg_port=data.get("PG_PORT", "5432"),
        external_url=data.get("PREFECT_API_DATABASE_CONNECTION_URL", "")
        if data.get("PO_SERVE_EXTERNAL", "") == "1"
        else "",
    )


def build_db_url(creds: ServeCreds) -> str:
    if creds.is_external():
        return creds.external_url
    pw = quote(creds.pg_password, safe="")
    return (
        f"postgresql+asyncpg://{creds.pg_user}:{pw}"
        f"@{creds.pg_host}:{creds.pg_port}/{creds.pg_db}"
    )


def save_creds(creds: ServeCreds) -> None:
    """Write ~/.config/po/serve.env with mode 0600 (parent dir 0700)."""
    CREDS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        CREDS_DIR.chmod(0o700)
    except OSError:
        pass
    db_url = build_db_url(creds)
    lines = [
        "# Generated by `po serve install` — DO NOT EDIT BY HAND.",
        "# Sourced by prefect-postgres.service and prefect-server.service.",
        f"POSTGRES_USER={creds.pg_user}",
        f"POSTGRES_PASSWORD={creds.pg_password}",
        f"POSTGRES_DB={creds.pg_db}",
        f"PG_HOST={creds.pg_host}",
        f"PG_PORT={creds.pg_port}",
        f"PREFECT_API_DATABASE_CONNECTION_URL={db_url}",
        f"PO_SERVE_EXTERNAL={'1' if creds.is_external() else '0'}",
        "",
    ]
    CREDS_FILE.write_text("\n".join(lines))
    CREDS_FILE.chmod(0o600)


def _detect_legacy_creds() -> ServeCreds | None:
    """If a v1 prefect-postgres container is running with hardcoded creds,
    return a ServeCreds matching it so we can migrate without breaking the
    user's existing data dir."""
    try:
        rc = subprocess.run(
            [
                "docker",
                "inspect",
                "prefect-postgres",
                "--format",
                "{{range .Config.Env}}{{println .}}{{end}}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if rc.returncode != 0:
        return None
    env = {}
    for line in rc.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            env[k] = v
    user = env.get("POSTGRES_USER")
    pw = env.get("POSTGRES_PASSWORD")
    db = env.get("POSTGRES_DB")
    if not (user and pw and db):
        return None
    return ServeCreds(pg_user=user, pg_password=pw, pg_db=db)


# ---- helpers ----------------------------------------------------------------


def _systemctl(*args: str) -> int:
    return subprocess.call(["systemctl", "--user", *args])


def _require(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        typer.echo(f"error: `{binary}` not on PATH", err=True)
        raise SystemExit(1)
    return path


def _data_dir_populated() -> bool:
    if not PG_DATA_DIR.exists():
        return False
    try:
        return any(PG_DATA_DIR.iterdir())
    except OSError:
        return False


# ---- install ----------------------------------------------------------------


@app.command()
def install(
    enable: bool = typer.Option(
        True, "--enable/--no-enable", help="Enable + start units after install."
    ),
    set_default_db: bool = typer.Option(
        True,
        "--set-default-db/--no-set-default-db",
        help="Run `prefect config set PREFECT_API_DATABASE_CONNECTION_URL=...` so PG is the default.",
    ),
    pg_user: str | None = typer.Option(None, "--pg-user", help="Postgres role name."),
    pg_password: str | None = typer.Option(
        None,
        "--pg-password",
        help="Postgres password (URL-safe charset only). Random if omitted on first install.",
    ),
    pg_db: str | None = typer.Option(None, "--pg-db", help="Postgres database name."),
    pg_host: str | None = typer.Option(
        None, "--pg-host", help="Host the PG container binds to (default 127.0.0.1)."
    ),
    pg_port: str | None = typer.Option(
        None, "--pg-port", help="Host port for PG (default 5432)."
    ),
    rotate_password: bool = typer.Option(
        False,
        "--rotate-password",
        help="Regenerate a random PG password even if creds file exists.",
    ),
    external_pg: str | None = typer.Option(
        None,
        "--external-pg",
        help="Skip local PG container; use this user-supplied PG URL instead.",
    ),
) -> None:
    """Install systemd-user units for Postgres + Prefect server.

    Requires: docker (unless --external-pg), prefect on PATH, systemd user
    session. Idempotent: reuses ~/.config/po/serve.env on re-run.
    """
    prefect_bin = _require("prefect")

    per_field_flags = (pg_user, pg_password, pg_db, pg_host, pg_port)
    if external_pg and any(per_field_flags):
        raise typer.BadParameter(
            "--external-pg is mutually exclusive with --pg-user/--pg-password/--pg-db/--pg-host/--pg-port"
        )

    creds = _resolve_creds(
        pg_user=pg_user,
        pg_password=pg_password,
        pg_db=pg_db,
        pg_host=pg_host,
        pg_port=pg_port,
        rotate_password=rotate_password,
        external_pg=external_pg,
    )
    save_creds(creds)
    typer.echo(f"wrote {CREDS_FILE} (mode 0600)")

    if not creds.is_external():
        _require("docker")

    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    if not creds.is_external():
        PG_DATA_DIR.mkdir(parents=True, exist_ok=True)
        PG_UNIT.write_text(PG_UNIT_TEMPLATE.format(creds_file=CREDS_FILE))
        typer.echo(f"wrote {PG_UNIT}")
        server_template = SERVER_UNIT_TEMPLATE_LOCAL
    else:
        # Don't write a fresh PG unit in external mode. Leave any pre-existing
        # one in place; uninstall handles removal.
        server_template = SERVER_UNIT_TEMPLATE_EXTERNAL
        typer.echo(f"external PG mode — skipping {PG_UNIT}")

    SERVER_UNIT.write_text(
        server_template.format(prefect_bin=prefect_bin, creds_file=CREDS_FILE)
    )
    typer.echo(f"wrote {SERVER_UNIT}")

    if rotate_password and not creds.is_external() and _data_dir_populated():
        typer.echo(
            "WARN: --rotate-password set but PG data dir is non-empty. The "
            "postgres image only honors POSTGRES_PASSWORD on first init; the "
            "running role's password will NOT change. Run `po serve uninstall "
            "--purge-data` first, or `ALTER USER ... PASSWORD` against the "
            "running container.",
            err=True,
        )

    if set_default_db:
        subprocess.run(
            [
                prefect_bin,
                "config",
                "set",
                f"PREFECT_API_DATABASE_CONNECTION_URL={build_db_url(creds)}",
            ],
            check=False,
        )
        subprocess.run(
            [prefect_bin, "config", "set", "PREFECT_API_URL=http://127.0.0.1:4200/api"],
            check=False,
        )
        typer.echo(
            "set PREFECT_API_DATABASE_CONNECTION_URL + PREFECT_API_URL on profile"
        )

    _systemctl("daemon-reload")

    if enable:
        if not creds.is_external():
            _systemctl("enable", "--now", "prefect-postgres.service")
            subprocess.run(
                [
                    "sh",
                    "-c",
                    "until docker exec prefect-postgres pg_isready "
                    f'-U "{creds.pg_user}" -d "{creds.pg_db}" '
                    ">/dev/null 2>&1; do sleep 1; done",
                ],
                check=False,
            )
        subprocess.run(
            [prefect_bin, "server", "database", "upgrade", "-y"], check=False
        )
        _systemctl("enable", "--now", "prefect-server.service")
        typer.echo("enabled + started units")
        typer.echo(
            "tip: `loginctl enable-linger $USER` so they survive logout (one-time)"
        )

    status()


def _resolve_creds(
    *,
    pg_user: str | None,
    pg_password: str | None,
    pg_db: str | None,
    pg_host: str | None,
    pg_port: str | None,
    rotate_password: bool,
    external_pg: str | None,
) -> ServeCreds:
    """Compute the ServeCreds for this install, honoring existing file +
    flag overrides + backward-compat detection."""
    if external_pg:
        parsed = urlparse(external_pg)
        if parsed.scheme not in {"postgresql", "postgresql+asyncpg", "postgres"}:
            raise typer.BadParameter(
                f"--external-pg URL must start with postgresql:// or postgresql+asyncpg://, got {external_pg!r}"
            )
        return ServeCreds(external_url=external_pg)

    existing = load_creds()
    if existing is None:
        existing = _detect_legacy_creds()
        if existing is not None:
            typer.echo(
                "detected pre-existing prefect-postgres container; reusing its creds.",
                err=True,
            )

    creds = existing or ServeCreds()

    if pg_user is not None:
        _validate_safe("--pg-user", pg_user)
        creds.pg_user = pg_user
    if pg_db is not None:
        _validate_safe("--pg-db", pg_db)
        creds.pg_db = pg_db
    if pg_host is not None:
        creds.pg_host = pg_host
    if pg_port is not None:
        creds.pg_port = pg_port
    if pg_password is not None:
        _validate_safe("--pg-password", pg_password)
        creds.pg_password = pg_password
    elif rotate_password or not creds.pg_password:
        creds.pg_password = secrets.token_urlsafe(32)

    return creds


# ---- uninstall --------------------------------------------------------------


@app.command()
def uninstall(
    purge_data: bool = typer.Option(
        False,
        "--purge-data",
        help="Also delete the Postgres data dir AND ~/.config/po/serve.env.",
    ),
) -> None:
    """Stop, disable, and remove the systemd units. Safe to re-run."""
    _systemctl("disable", "--now", "prefect-server.service")
    _systemctl("disable", "--now", "prefect-postgres.service")
    subprocess.run(["docker", "rm", "-f", "prefect-postgres"], check=False)
    for unit in (SERVER_UNIT, PG_UNIT):
        if unit.exists():
            unit.unlink()
            typer.echo(f"removed {unit}")
    _systemctl("daemon-reload")
    if purge_data:
        shutil.rmtree(PG_DATA_DIR, ignore_errors=True)
        typer.echo(f"purged {PG_DATA_DIR}")
        if CREDS_FILE.exists():
            CREDS_FILE.unlink()
            typer.echo(f"removed {CREDS_FILE}")
        if CREDS_DIR.exists():
            try:
                CREDS_DIR.rmdir()
            except OSError:
                pass  # directory not empty — leave it alone
    typer.echo("uninstalled")


# ---- status -----------------------------------------------------------------


@app.command()
def status() -> None:
    """Show is-active state for both units + curl the API + DB ping."""
    creds = load_creds()
    if creds is None:
        typer.echo("  creds: (no ~/.config/po/serve.env — run `po serve install`)")
    elif creds.is_external():
        typer.echo(f"  creds: external PG → {creds.external_url}")
    else:
        typer.echo(
            f"  creds: {creds.pg_user}@{creds.pg_host}:{creds.pg_port}/{creds.pg_db}"
        )

    for unit in ("prefect-postgres.service", "prefect-server.service"):
        rc = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            capture_output=True,
            text=True,
        )
        state = (rc.stdout or "").strip() or "unknown"
        typer.echo(f"  {unit}: {state}")
    api = subprocess.run(
        [
            "curl",
            "-s",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "http://127.0.0.1:4200/api/health",
        ],
        capture_output=True,
        text=True,
    ).stdout.strip()
    typer.echo(f"  /api/health: HTTP {api}")
    if creds is not None and creds.is_external():
        typer.echo("  pg_isready: (external — skipped)")
        return
    pg_user = creds.pg_user if creds else "prefect"
    pg_db = creds.pg_db if creds else "prefect"
    pg = subprocess.run(
        [
            "docker",
            "exec",
            "prefect-postgres",
            "pg_isready",
            "-U",
            pg_user,
            "-d",
            pg_db,
        ],
        capture_output=True,
        text=True,
    )
    typer.echo(f"  pg_isready: {(pg.stdout or pg.stderr).strip()}")
