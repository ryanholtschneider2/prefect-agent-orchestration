"""Orchestrate `po run --env <name>`: rig push, identity sync, dispatch, mirror-back.

Steps in `run_with_env`:
  1. Read envs/<name>.toml; error if missing.
  2. git push po-env-<name> HEAD (skip if already up to date).
  3. Re-upload identity bundle iff local hash differs.
  4. Stamp po.env_name on the bead (best-effort).
  5. Schedule <formula>-env-<name>-manual deployment; poll to terminal.
  6. Mirror <rig>/.planning/<formula>/<issue>/ back via driver.fs_download.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer

from prefect_orchestration.env import (
    EnvNotFound,
    EnvRecord,
    _build_identity_tarball,
    delete_env,
    read_env,
    write_env,
)
from prefect_orchestration.env_drivers import EnvHandle, load_drivers


def run_with_env(
    *,
    env_name: str,
    formula: str,
    kwargs: dict[str, Any],
    rebuild: bool = False,
    issue_id: str | None = None,
    rig_path: Path | None = None,
) -> str | None:
    """Orchestrate a `po run --env <name>` dispatch. Returns the terminal state name."""
    try:
        record = read_env(env_name)
    except EnvNotFound:
        typer.echo(f"error: no env '{env_name}'; run `po env up` first", err=True)
        raise typer.Exit(1)

    drivers = load_drivers()
    if record.driver not in drivers:
        typer.echo(
            f"error: driver '{record.driver}' not registered; cannot dispatch",
            err=True,
        )
        raise typer.Exit(1)

    drv = drivers[record.driver]
    handle = EnvHandle(driver_name=record.driver, opaque=record.opaque)

    if rebuild:
        typer.echo(f"rebuilding env '{env_name}'...")
        drv.provision(env_name, record.snapshot_tag, {"rebuild": True})

    _push_rig(record, rig_path)
    _maybe_push_identity(record, handle, drv)

    if issue_id:
        _stamp_bead(issue_id, env_name)

    terminal_state = _run_async_dispatch(record, formula, kwargs, issue_id)
    typer.echo(f"[po] flow terminal: {terminal_state}")

    if issue_id and rig_path:
        remote_path = _remote_run_dir(formula, issue_id)
        local_path = rig_path / ".planning" / formula / issue_id
        try:
            drv.fs_download(handle, remote_path, local_path)
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"warning: mirror-back failed: {exc}", err=True)

    record.last_run_at = datetime.now(timezone.utc).isoformat()
    write_env(record)
    return terminal_state


def _run_async_dispatch(
    record: EnvRecord,
    formula: str,
    kwargs: dict[str, Any],
    issue_id: str | None,
) -> str:
    """Run the async dispatch coroutine. Separate function to allow test patching."""
    return asyncio.run(_dispatch(record, formula, kwargs, issue_id))


def _push_rig(record: EnvRecord, rig_path: Path | None) -> None:
    """Push the local rig to po-env-<name> git remote."""
    if not record.rig_remote:
        typer.echo(
            "info: rig_remote not set (tar transport); skipping git push", err=True
        )
        return
    if rig_path is None:
        return

    remote = f"po-env-{record.name}"
    cwd = str(rig_path)

    # Add remote if not already present (ignore error when it exists)
    subprocess.run(
        ["git", "remote", "add", remote, record.rig_remote],
        cwd=cwd,
        capture_output=True,
    )

    # Fetch to get remote HEAD; treat failures as "push needed"
    fetch = subprocess.run(["git", "fetch", remote], cwd=cwd, capture_output=True)

    if fetch.returncode == 0:
        diff = subprocess.run(
            ["git", "diff", f"refs/remotes/{remote}/HEAD", "HEAD", "--quiet"],
            cwd=cwd,
            capture_output=True,
        )
        if diff.returncode == 0:
            typer.echo(
                f"info: rig already up to date on {remote}; skipping push", err=True
            )
            return

    push = subprocess.run(
        ["git", "push", remote, "HEAD"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if push.returncode != 0:
        typer.echo(
            f"warning: git push to {remote} failed: {push.stderr.strip()}", err=True
        )


def _maybe_push_identity(record: EnvRecord, handle: EnvHandle, driver: Any) -> None:
    """Re-upload identity tarball iff local sha256 differs from stored hash."""
    claude_dir = Path.home() / ".claude"
    if not claude_dir.exists():
        return
    with tempfile.TemporaryDirectory() as tmp:
        try:
            tarball_path, sha256 = _build_identity_tarball(Path(tmp), with_auth=False)
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"warning: identity tarball build failed: {exc}", err=True)
            return
        if sha256 == record.identity_hash:
            return
        try:
            driver.push_identity(handle, tarball_path, sha256)
            record.identity_hash = sha256
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"warning: identity push failed: {exc}", err=True)


def _stamp_bead(issue_id: str, env_name: str) -> None:
    """Write po.env_name=<env_name> onto the bead (best-effort)."""
    result = subprocess.run(
        ["bd", "update", issue_id, "--set-metadata", f"po.env_name={env_name}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        typer.echo(
            f"warning: could not stamp po.env_name: {result.stderr.strip()}", err=True
        )


def _remote_run_dir(formula: str, issue_id: str) -> str:
    """Relative path to the run_dir on the remote rig."""
    return f".planning/{formula}/{issue_id}"


async def _dispatch(
    record: EnvRecord,
    formula: str,
    kwargs: dict[str, Any],
    issue_id: str | None,
) -> str:
    """Submit a scheduled run, stream state events, return terminal state name."""
    from prefect import get_client

    from prefect_orchestration.scheduling import submit_scheduled_run
    from prefect_orchestration.watch import (
        _TERMINAL_STATES,
        _state_name_of,
        _state_type_of,
    )

    async with get_client() as client:
        flow_run, full_name, warn_msg = await submit_scheduled_run(
            client=client,
            formula=formula,
            parameters=kwargs,
            scheduled_time=datetime.now(timezone.utc),
            issue_id=issue_id,
            work_pool_override=record.pool,
            env_name=record.name,
        )
        if warn_msg:
            print(warn_msg, file=sys.stderr)

        typer.echo(f"[po] dispatched: {full_name} (id={getattr(flow_run, 'id', '?')})")

        flow_run_id = getattr(flow_run, "id", None)
        prev_state: str | None = None

        while True:
            try:
                fr = await client.read_flow_run(flow_run_id)
            except Exception as exc:  # noqa: BLE001
                print(f"[po] poll error: {exc}", file=sys.stderr)
                await asyncio.sleep(3.0)
                continue

            state_name = _state_name_of(fr)
            state_type = _state_type_of(fr)

            if state_name != prev_state:
                typer.echo(f"[prefect] → {state_name}")
                prev_state = state_name

            if state_type in _TERMINAL_STATES:
                return state_name or state_type or "?"

            await asyncio.sleep(3.0)


def run_ephemeral_env(
    *,
    driver_name: str,
    formula: str,
    kwargs: dict[str, Any],
    snapshot: str = "",
    with_auth: bool = False,
    auto_down_secs: float = 1800.0,
    auto_down_on_failure: bool = False,
    issue_id: str | None = None,
    rig_path: Path | None = None,
    rebuild: bool = False,
) -> None:
    """Provision an ephemeral env, run formula, teardown based on terminal state.

    On flow Completed (or auto_down_on_failure=True on Failed), waits
    auto_down_secs then tears down the env and removes its record.
    On flow Failed with auto_down_on_failure=False, leaves the env alive
    so the operator can `po attach` and investigate.
    """
    drivers = load_drivers()
    if driver_name not in drivers:
        typer.echo(
            f"error: unknown driver '{driver_name}'; registered: {sorted(drivers)}",
            err=True,
        )
        raise typer.Exit(1)

    drv = drivers[driver_name]
    auto_id = f"ephemeral-{secrets.token_hex(4)}"
    now = datetime.now(timezone.utc).isoformat()

    # Optional image build (drivers that don't implement it are silently skipped)
    if hasattr(drv, "build_image"):
        drv.build_image()

    typer.echo(
        f"[po] provisioning ephemeral env '{auto_id}' via driver '{driver_name}'..."
    )
    handle = drv.provision(auto_id, snapshot, {})
    rig_remote = drv.ensure_rig_remote(handle)

    identity_hash = ""
    claude_dir = Path.home() / ".claude"
    if claude_dir.exists():
        with tempfile.TemporaryDirectory() as tmp:
            try:
                tarball_path, identity_hash = _build_identity_tarball(
                    Path(tmp), with_auth=with_auth
                )
                drv.push_identity(handle, tarball_path, identity_hash)
            except Exception as exc:  # noqa: BLE001
                typer.echo(f"warning: identity push failed: {exc}", err=True)

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
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"warning: credential push failed: {exc}", err=True)

    pool_name = f"po-env-{auto_id}"
    drv.start_worker(handle, pool_name)
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

    record = EnvRecord(
        name=auto_id,
        driver=driver_name,
        snapshot_tag=snapshot,
        pool=pool_name,
        opaque=dict(handle.opaque),
        rig_remote=rig_remote,
        identity_hash=identity_hash,
        created_at=now,
        last_run_at="",
    )
    write_env(record)

    terminal_state: str = "Unknown"
    try:
        terminal_state = (
            run_with_env(
                env_name=auto_id,
                formula=formula,
                kwargs=kwargs,
                rebuild=rebuild,
                issue_id=issue_id,
                rig_path=rig_path,
            )
            or "Unknown"
        )
    finally:
        should_down = (terminal_state == "Completed") or auto_down_on_failure
        if should_down:
            if auto_down_secs > 0:
                typer.echo(
                    f"[po] grace window {auto_down_secs:.0f}s before teardown..."
                )
                time.sleep(auto_down_secs)
            typer.echo(f"[po] tearing down ephemeral env '{auto_id}'...")
            try:
                drv.teardown(handle)
            except Exception as exc:  # noqa: BLE001
                typer.echo(f"warning: teardown error: {exc}", err=True)
            delete_env(auto_id)
            typer.echo(f"[po] ephemeral env '{auto_id}' removed")
        else:
            typer.echo(
                f"[po] flow {terminal_state}: ephemeral env '{auto_id}' kept alive "
                f"(pass --auto-down-on-failure to tear down on failure)"
            )


__all__ = ["run_ephemeral_env", "run_with_env"]
