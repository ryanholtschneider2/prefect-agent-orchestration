"""`po env` sub-app — manage remote cloud envs via registered EnvDrivers.

Env records live at ~/.config/po/envs/<name>.toml (flat TOML). The `env_app`
Typer sub-app is wired into `po` in cli.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer

from prefect_orchestration.env_drivers import EnvHandle, load_drivers

ENVS_DIR = Path.home() / ".config" / "po" / "envs"

# Curated ~/.claude/ subset for identity tarballs
_CLAUDE_INCLUDE = {
    "settings.json",
    "CLAUDE.md",
    ".mcp.json",
    "commands",
    "prompts",
    "skills",
    "memory",
}
# Excluded: projects/, todos/, statsig/, caches, .credentials.json (unless --with-auth)
_CLAUDE_EXCLUDE = {"projects", "todos", "statsig", ".credentials.json"}

_NAME_RE_DESC = "must match [A-Za-z0-9_-]+"
_SAFE_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")


class EnvNotFound(Exception):
    pass


def _validate_name(name: str) -> None:
    if not name or not all(c in _SAFE_CHARS for c in name):
        typer.echo(f"error: env name '{name}' {_NAME_RE_DESC}", err=True)
        raise typer.Exit(1)


@dataclass
class EnvRecord:
    name: str
    driver: str
    snapshot_tag: str
    pool: str
    opaque: dict[str, Any]
    rig_remote: str
    identity_hash: str
    created_at: str  # ISO-8601
    last_run_at: str  # ISO-8601 or ""


def _toml_str(v: Any) -> str:
    """Minimal TOML value serializer for strings and JSON blobs."""
    if isinstance(v, str):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(v, (dict, list)):
        escaped = json.dumps(v).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return str(v)


def write_env(record: EnvRecord) -> None:
    ENVS_DIR.mkdir(parents=True, exist_ok=True)
    path = ENVS_DIR / f"{record.name}.toml"
    lines = [
        f"name = {_toml_str(record.name)}",
        f"driver = {_toml_str(record.driver)}",
        f"snapshot_tag = {_toml_str(record.snapshot_tag)}",
        f"pool = {_toml_str(record.pool)}",
        f"opaque = {_toml_str(record.opaque)}",
        f"rig_remote = {_toml_str(record.rig_remote)}",
        f"identity_hash = {_toml_str(record.identity_hash)}",
        f"created_at = {_toml_str(record.created_at)}",
        f"last_run_at = {_toml_str(record.last_run_at)}",
    ]
    path.write_text("\n".join(lines) + "\n")


def _parse_toml_str(raw: str) -> str:
    """Extract the string value from a quoted TOML string line value."""
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        inner = raw[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    return raw


def _parse_toml_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        result[key.strip()] = _parse_toml_str(val.strip())
    return result


def _deserialize_opaque(v: str) -> dict[str, Any]:
    try:
        return json.loads(v)  # type: ignore[return-value]
    except Exception:
        return {}


def read_env(name: str) -> EnvRecord:
    path = ENVS_DIR / f"{name}.toml"
    if not path.exists():
        raise EnvNotFound(name)
    data = _parse_toml_file(path)
    return EnvRecord(
        name=data.get("name", name),
        driver=data.get("driver", ""),
        snapshot_tag=data.get("snapshot_tag", ""),
        pool=data.get("pool", ""),
        opaque=_deserialize_opaque(data.get("opaque", "{}")),
        rig_remote=data.get("rig_remote", ""),
        identity_hash=data.get("identity_hash", ""),
        created_at=data.get("created_at", ""),
        last_run_at=data.get("last_run_at", ""),
    )


def list_envs() -> list[EnvRecord]:
    if not ENVS_DIR.exists():
        return []
    records = []
    for p in sorted(ENVS_DIR.glob("*.toml")):
        try:
            records.append(read_env(p.stem))
        except Exception:
            pass
    return records


def delete_env(name: str) -> None:
    path = ENVS_DIR / f"{name}.toml"
    if path.exists():
        path.unlink()


def compute_identity_hash(*, with_auth: bool = False) -> str:
    """Return the sha256 of the current curated ~/.claude/ tarball."""
    with tempfile.TemporaryDirectory() as tmp:
        _, sha256 = _build_identity_tarball(Path(tmp), with_auth=with_auth)
        return sha256


def _build_identity_tarball(dest: Path, *, with_auth: bool) -> tuple[Path, str]:
    """Tar the curated ~/.claude/ subset; return (tarball_path, sha256_hex)."""
    claude_dir = Path.home() / ".claude"
    tarball = dest / "claude-identity.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        if claude_dir.exists():
            for child in sorted(claude_dir.iterdir()):
                name = child.name
                if name in _CLAUDE_EXCLUDE and not (
                    name == ".credentials.json" and with_auth
                ):
                    continue
                if name in _CLAUDE_INCLUDE or (
                    with_auth and name == ".credentials.json"
                ):
                    tf.add(child, arcname=f".claude/{name}", recursive=True)
    sha256 = hashlib.sha256(tarball.read_bytes()).hexdigest()
    return tarball, sha256


# ---------------------------------------------------------------------------
# env_app
# ---------------------------------------------------------------------------

env_app = typer.Typer(
    name="env",
    help="Manage remote cloud envs (provision, list, teardown, attach).",
    no_args_is_help=True,
)


@env_app.command("up")
def env_up(
    driver: str = typer.Option(
        ..., help="Registered driver name (e.g. noop, rclaude)."
    ),
    name: str = typer.Option("default", help="Env name (stored in envs/<name>.toml)."),
    snapshot: str = typer.Option("", help="Driver-opaque snapshot tag."),
    with_auth: bool = typer.Option(
        False, help="Include ~/.claude/.credentials.json in identity push."
    ),
    rebuild: bool = typer.Option(False, help="Force rebuild of identity tarball."),
    rig_transport: str = typer.Option("git", help="Rig transport: git or tar."),
    backend: str = typer.Option(
        "", help="Driver-specific backend (e.g. digitalocean, hetzner)."
    ),
) -> None:
    """Provision a remote env and persist its record."""
    _validate_name(name)
    drivers = load_drivers()
    if driver not in drivers:
        registered = sorted(drivers.keys())
        typer.echo(
            f"error: unknown driver '{driver}'; registered: {registered}",
            err=True,
        )
        raise typer.Exit(1)

    drv = drivers[driver]
    now = datetime.now(timezone.utc).isoformat()

    # 1. Provision
    typer.echo(f"provisioning env '{name}' via driver '{driver}'...")
    provision_opts: dict[str, str] = {"rig_transport": rig_transport}
    if backend:
        provision_opts["backend"] = backend
    handle = drv.provision(name, snapshot, provision_opts)

    # 2. Ensure rig remote
    rig_remote = drv.ensure_rig_remote(handle)

    # 3. Push identity
    identity_hash = ""
    claude_dir = Path.home() / ".claude"
    if claude_dir.exists():
        with tempfile.TemporaryDirectory() as tmp:
            try:
                tarball_path, identity_hash = _build_identity_tarball(
                    Path(tmp), with_auth=with_auth
                )
                drv.push_identity(handle, tarball_path, identity_hash)
            except Exception as exc:
                typer.echo(f"warning: identity push failed: {exc}", err=True)
    else:
        typer.echo("warning: ~/.claude/ not found; skipping identity push", err=True)

    # 4. Push credentials
    env_dict: dict[str, str] = {}
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        env_dict["ANTHROPIC_API_KEY"] = api_key
    oauth_creds: bytes | None = None
    creds_path = Path.home() / ".claude" / ".credentials.json"
    if with_auth and creds_path.exists():
        oauth_creds = creds_path.read_bytes()
    if env_dict or oauth_creds is not None:
        try:
            drv.push_credentials(handle, env_dict, oauth_creds)
        except Exception as exc:
            typer.echo(f"warning: credential push failed: {exc}", err=True)
    else:
        typer.echo(
            "warning: no ANTHROPIC_API_KEY set and --with-auth not passed; "
            "no credentials pushed",
            err=True,
        )

    # 5. Start worker
    pool_name = f"po-env-{name}"
    drv.start_worker(handle, pool_name)

    # 6. Create Prefect work pool (idempotent; warn on failure)
    try:
        subprocess.run(
            ["prefect", "work-pool", "create", pool_name, "--type", "process"],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError:
        typer.echo("warning: prefect not on PATH; work pool not created", err=True)
    except subprocess.CalledProcessError:
        typer.echo(
            f"warning: work pool not created (Prefect server unreachable); "
            f"run manually: prefect work-pool create {pool_name}",
            err=True,
        )

    # 7. Persist
    record = EnvRecord(
        name=name,
        driver=driver,
        snapshot_tag=snapshot,
        pool=pool_name,
        opaque=dict(handle.opaque),
        rig_remote=rig_remote,
        identity_hash=identity_hash,
        created_at=now,
        last_run_at="",
    )
    write_env(record)
    typer.echo(f"env '{name}' up  →  {ENVS_DIR / (name + '.toml')}")


@env_app.command("list")
def env_list(
    idle: bool = typer.Option(False, help="Show only idle envs."),
    threshold: str = typer.Option("1h", help="Idle threshold (e.g. 1h, 30m)."),
) -> None:
    """List all provisioned envs."""
    records = list_envs()
    if not records:
        typer.echo("no envs provisioned; run 'po env up' first")
        return

    # Simple table
    header = f"{'NAME':<20} {'DRIVER':<12} {'POOL':<24} {'CREATED':<26} {'LAST_RUN'}"
    typer.echo(header)
    typer.echo("-" * len(header))
    for r in records:
        typer.echo(
            f"{r.name:<20} {r.driver:<12} {r.pool:<24} {r.created_at:<26} {r.last_run_at or '-'}"
        )


@env_app.command("down")
def env_down(
    name: str = typer.Argument(..., help="Env name to tear down."),
    force: bool = typer.Option(False, "-f", help="Skip confirmation."),
) -> None:
    """Tear down a provisioned env and remove its record."""
    try:
        record = read_env(name)
    except EnvNotFound:
        typer.echo(f"error: no env '{name}'; run po env up first", err=True)
        raise typer.Exit(1)

    if not force:
        confirm = typer.confirm(f"Tear down env '{name}'?")
        if not confirm:
            raise typer.Exit(0)

    drivers = load_drivers()
    if record.driver in drivers:
        handle = EnvHandle(driver_name=record.driver, opaque=record.opaque)
        try:
            drivers[record.driver].teardown(handle)
        except Exception as exc:
            typer.echo(f"warning: teardown error: {exc}", err=True)
    else:
        typer.echo(
            f"warning: driver '{record.driver}' not registered; skipping teardown",
            err=True,
        )

    delete_env(name)
    typer.echo(f"env '{name}' removed")


@env_app.command("attach")
def env_attach(
    name: str = typer.Argument(..., help="Env name to attach to."),
    role: str = typer.Option("", help="Role/tmux session to attach to."),
) -> None:
    """Attach into a provisioned env (delegates to driver.attach_argv + execvp)."""
    try:
        record = read_env(name)
    except EnvNotFound:
        typer.echo(f"error: no env '{name}'; run po env up first", err=True)
        raise typer.Exit(1)

    drivers = load_drivers()
    if record.driver not in drivers:
        typer.echo(
            f"error: driver '{record.driver}' not registered; cannot attach",
            err=True,
        )
        raise typer.Exit(1)

    handle = EnvHandle(driver_name=record.driver, opaque=record.opaque)
    argv = drivers[record.driver].attach_argv(handle, role, "")
    if not argv:
        typer.echo("error: driver returned empty attach argv", err=True)
        raise typer.Exit(1)
    os.execvp(argv[0], argv)


@env_app.command("reap")
def env_reap(
    idle_since: str = typer.Option(
        "24h", help="Tear down envs idle for this long (e.g. 24h, 0)."
    ),
    yes: bool = typer.Option(False, "-y", help="Skip confirmation."),
) -> None:
    """Tear down idle envs."""
    records = list_envs()
    if not records:
        typer.echo("no envs to reap")
        return

    # Parse idle_since into seconds
    idle_secs = _parse_duration(idle_since)
    now = datetime.now(timezone.utc)

    to_reap: list[EnvRecord] = []
    for r in records:
        if idle_secs == 0:
            to_reap.append(r)
            continue
        # Use last_run_at if set, else created_at
        ts_str = r.last_run_at or r.created_at
        if not ts_str:
            to_reap.append(r)
            continue
        try:
            ts = datetime.fromisoformat(ts_str)
            if (now - ts).total_seconds() >= idle_secs:
                to_reap.append(r)
        except ValueError:
            pass

    if not to_reap:
        typer.echo("no idle envs to reap")
        return

    typer.echo(f"envs to reap: {[r.name for r in to_reap]}")
    if not yes:
        confirm = typer.confirm("Proceed with teardown?")
        if not confirm:
            raise typer.Exit(0)

    drivers = load_drivers()
    for r in to_reap:
        typer.echo(f"  tearing down '{r.name}'...")
        if r.driver in drivers:
            handle = EnvHandle(driver_name=r.driver, opaque=r.opaque)
            try:
                drivers[r.driver].teardown(handle)
            except Exception as exc:
                typer.echo(f"  warning: {exc}", err=True)
        delete_env(r.name)
        typer.echo(f"  removed '{r.name}'")


def _parse_duration(s: str) -> float:
    """Parse a human duration string like '24h', '30m', '0' into seconds."""
    s = s.strip()
    if s == "0":
        return 0.0
    if s.endswith("h"):
        return float(s[:-1]) * 3600
    if s.endswith("m"):
        return float(s[:-1]) * 60
    if s.endswith("s"):
        return float(s[:-1])
    return float(s)


__all__ = [
    "ENVS_DIR",
    "EnvNotFound",
    "EnvRecord",
    "compute_identity_hash",
    "delete_env",
    "env_app",
    "list_envs",
    "read_env",
    "write_env",
]
