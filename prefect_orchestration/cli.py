"""`po` CLI — discovers formulas via the `po.formulas` entry-point group.

Formula packs declare their flows in their own `pyproject.toml`:

    [project.entry-points."po.formulas"]
    software-dev-full = "po_formulas.software_dev:software_dev_full"
    epic = "po_formulas.epic:epic_run"

After `uv add <pack>`, `po list` shows every registered formula and
`po run <name> --key=value ...` invokes it. Core has no knowledge of
specific formulas — they're pluggable.
"""

from __future__ import annotations

import inspect
import sys
from importlib.metadata import entry_points
from typing import Any

import typer

from prefect_orchestration import deployments as _deployments

app = typer.Typer(
    help="Prefect orchestration for Claude Code agents — pluggable formula runner.",
    no_args_is_help=True,
)


@app.callback()
def _root() -> None:
    """Root — forces Typer to keep subcommand form."""


def _load_formulas() -> dict[str, Any]:
    """Return {name: loaded_flow_object} for every `po.formulas` entry point."""
    formulas: dict[str, Any] = {}
    try:
        eps = entry_points(group="po.formulas")
    except TypeError:
        # Older importlib.metadata API (pre 3.10)
        eps = entry_points().get("po.formulas", [])  # type: ignore[assignment]
    for ep in eps:
        try:
            formulas[ep.name] = ep.load()
        except Exception as exc:
            typer.echo(f"warning: failed to load formula {ep.name}: {exc}", err=True)
    return formulas


def _coerce(value: str) -> Any:
    """Coerce a CLI string to bool/int/None when unambiguous, else keep str."""
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("none", "null"):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _parse_kwargs(extras: list[str]) -> dict[str, Any]:
    """Parse `--key value` / `--key=value` / `--flag` into kwargs.

    Bare `--flag` becomes `flag=True`. `--key=value` and `--key value`
    both work. All values are passed to `_coerce` for light typing.
    """
    out: dict[str, Any] = {}
    i = 0
    while i < len(extras):
        tok = extras[i]
        if not tok.startswith("--"):
            raise typer.BadParameter(f"expected `--key`, got {tok!r}")
        tok = tok[2:]
        # `--no-flag` → `flag=False`. Stays compatible with Typer conventions.
        if tok.startswith("no-") and "=" not in tok:
            out[tok[3:].replace("-", "_")] = False
            i += 1
            continue
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k.replace("-", "_")] = _coerce(v)
            i += 1
        elif i + 1 < len(extras) and not extras[i + 1].startswith("--"):
            out[tok.replace("-", "_")] = _coerce(extras[i + 1])
            i += 2
        else:
            out[tok.replace("-", "_")] = True
            i += 1
    return out


@app.command(name="list")
def list_formulas() -> None:
    """List formulas registered via the `po.formulas` entry-point group."""
    formulas = _load_formulas()
    if not formulas:
        typer.echo("no formulas installed.")
        typer.echo("install a pack with `uv add <pack>` or `pip install <pack>`")
        typer.echo("packs declare formulas via `[project.entry-points.\"po.formulas\"]`.")
        return
    for name, flow_obj in sorted(formulas.items()):
        fn_name = getattr(flow_obj, "__name__", str(flow_obj))
        module = getattr(flow_obj, "__module__", "?")
        doc = (inspect.getdoc(flow_obj) or "").split("\n", 1)[0]
        typer.echo(f"  {name:28s}  {module}:{fn_name}")
        if doc:
            typer.echo(f"  {'':28s}  {doc}")


@app.command()
def show(name: str) -> None:
    """Show the signature + docstring of a registered formula."""
    formulas = _load_formulas()
    if name not in formulas:
        typer.echo(f"no formula named {name!r}. Run `po list`.", err=True)
        raise typer.Exit(1)
    flow_obj = formulas[name]
    # Prefect wraps the fn; the original is at .fn
    fn = getattr(flow_obj, "fn", flow_obj)
    typer.echo(f"{name} — {flow_obj.__module__}:{flow_obj.__name__}")
    sig = inspect.signature(fn)
    typer.echo(f"\nSignature:\n  {fn.__name__}{sig}")
    doc = inspect.getdoc(fn)
    if doc:
        typer.echo(f"\nDoc:\n{doc}")


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def run(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Formula name from `po list`"),
) -> None:
    """Run a registered formula. Pass flow kwargs after the name:

        po run software-dev-full --issue-id sr-8yu.3 --rig site --rig-path ./site
    """
    formulas = _load_formulas()
    if name not in formulas:
        typer.echo(f"no formula named {name!r}. Run `po list`.", err=True)
        raise typer.Exit(1)
    flow_obj = formulas[name]
    kwargs = _parse_kwargs(list(ctx.args))
    try:
        result = flow_obj(**kwargs)
    except TypeError as exc:
        typer.echo(f"bad arguments for {name}: {exc}", err=True)
        typer.echo(f"run `po show {name}` to see the signature", err=True)
        raise typer.Exit(2) from exc
    typer.echo(result)


@app.command()
def deploy(
    apply: bool = typer.Option(
        False, "--apply", help="Create/update deployments on the Prefect server."
    ),
    pack: str | None = typer.Option(None, "--pack", help="Only include this pack."),
    name: str | None = typer.Option(None, "--name", help="Only include this deployment name."),
    work_pool: str | None = typer.Option(
        None, "--work-pool", help="Assign this work pool to each deployment before apply."
    ),
) -> None:
    """List (or apply) deployments registered via the `po.deployments` EP group."""
    loaded, errors = _deployments.load_deployments()

    for err in errors:
        typer.echo(f"warning: {err.pack}: {err.error}", err=True)

    if pack is not None:
        loaded = [d for d in loaded if d.pack == pack]
    if name is not None:
        loaded = [d for d in loaded if getattr(d.deployment, "name", None) == name]

    if not loaded:
        typer.echo("no deployments registered.")
        typer.echo(
            "packs declare deployments via `[project.entry-points.\"po.deployments\"]` "
            "pointing at a `register()` callable that returns RunnerDeployment objects."
        )
        raise typer.Exit(0 if not errors else 1)

    if not apply:
        _print_deployment_table(loaded)
        raise typer.Exit(1 if errors else 0)

    # --apply path
    if not _deployments.prefect_api_configured():
        typer.echo(
            "PREFECT_API_URL is not set — point it at a running Prefect server "
            "(e.g. `prefect server start` → http://127.0.0.1:4200/api).",
            err=True,
        )
        raise typer.Exit(2)

    failures = 0
    for item in loaded:
        label = f"{item.pack}:{getattr(item.deployment, 'name', '?')}"
        try:
            dep_id = _deployments.apply_deployment(item.deployment, work_pool=work_pool)
        except Exception as exc:
            typer.echo(f"  FAIL  {label}  ({exc})", err=True)
            failures += 1
            continue
        typer.echo(f"  OK    {label}  → {dep_id}")
    if failures or errors:
        raise typer.Exit(1)


def _print_deployment_table(loaded: list[_deployments.LoadedDeployment]) -> None:
    rows = []
    for item in loaded:
        dep = item.deployment
        params = getattr(dep, "parameters", {}) or {}
        rows.append(
            (
                item.pack,
                getattr(dep, "name", "?"),
                getattr(dep, "flow_name", "?"),
                _deployments.format_schedule(dep),
                ",".join(sorted(params.keys())) or "-",
            )
        )
    headers = ("PACK", "DEPLOYMENT", "FLOW", "SCHEDULE", "PARAMS")
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    typer.echo(fmt.format(*headers))
    typer.echo(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        typer.echo(fmt.format(*row))


if __name__ == "__main__":
    app()
