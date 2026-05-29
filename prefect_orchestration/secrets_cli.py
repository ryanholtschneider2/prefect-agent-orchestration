"""`po secrets` — manage the encrypted local secret store (see secrets_store)."""

from __future__ import annotations

from pathlib import Path

import typer

from prefect_orchestration import secrets_store as _store

secrets_app = typer.Typer(
    name="secrets",
    help="Manage encrypted secrets injected into --env runs at spawn time.",
    no_args_is_help=True,
)


def _scope(env: str | None) -> str:
    return env if env else _store.GLOBAL


@secrets_app.command("set")
def secrets_set(
    assignment: str = typer.Argument(
        ..., help="KEY=VALUE, or KEY (with VALUE as the next arg)."
    ),
    value: str | None = typer.Argument(None, help="Value when KEY given alone."),
    env: str | None = typer.Option(
        None, "--env", help="Scope to this env (default: global)."
    ),
) -> None:
    """Store a secret (global, or scoped to an env)."""
    if "=" in assignment and value is None:
        key, _, val = assignment.partition("=")
    else:
        key, val = assignment, value
    if not key or val is None:
        typer.echo("error: provide KEY=VALUE or KEY VALUE", err=True)
        raise typer.Exit(2)
    try:
        _store.set_secret(key, val, scope=_scope(env))
    except _store.SecretsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(f"set {key} ({_scope(env)})")


@secrets_app.command("get")
def secrets_get(
    key: str = typer.Argument(...),
    env: str | None = typer.Option(None, "--env"),
) -> None:
    """Print a secret's value (explicit reveal)."""
    val = _store.get_secret(key, scope=_scope(env))
    if val is None:
        typer.echo(f"no secret {key!r} in scope {_scope(env)}", err=True)
        raise typer.Exit(1)
    typer.echo(val)


@secrets_app.command("list")
def secrets_list(
    env: str | None = typer.Option(None, "--env", help="Restrict to one scope."),
) -> None:
    """List secret KEYS by scope (values never shown)."""
    listing = _store.list_secrets(scope=env if env else None)
    for scope, keys in listing.items():
        typer.echo(f"[{scope}]")
        for k in keys:
            typer.echo(f"  {k}")
        if not keys:
            typer.echo("  (none)")


@secrets_app.command("rm")
def secrets_rm(
    key: str = typer.Argument(...),
    env: str | None = typer.Option(None, "--env"),
) -> None:
    """Delete a secret from a scope."""
    if _store.delete_secret(key, scope=_scope(env)):
        typer.echo(f"removed {key} ({_scope(env)})")
    else:
        typer.echo(f"no secret {key!r} in scope {_scope(env)}", err=True)
        raise typer.Exit(1)


@secrets_app.command("import")
def secrets_import(
    path: Path = typer.Argument(..., help="Path to a .env file (KEY=VALUE lines)."),
    env: str | None = typer.Option(None, "--env"),
) -> None:
    """Bulk-import KEY=VALUE lines from a .env file."""
    try:
        n = _store.import_env(path, scope=_scope(env))
    except _store.SecretsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(f"imported {n} secret(s) into scope {_scope(env)}")
