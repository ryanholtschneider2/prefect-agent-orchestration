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
import json
import os
import shutil
import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

import typer

from prefect_orchestration import artifacts as _artifacts
from prefect_orchestration import account as _account
from prefect_orchestration import attach as _attach
from prefect_orchestration import beads_meta as _beads_meta
from prefect_orchestration import trace as _trace
from prefect_orchestration import commands as _commands
from prefect_orchestration import deployments as _deployments
from prefect_orchestration import doctor as _doctor
from prefect_orchestration import env as _env
from prefect_orchestration import packs as _packs
from prefect_orchestration import resume as _resume
from prefect_orchestration import retry as _retry
from prefect_orchestration import run_lookup as _run_lookup
from prefect_orchestration import scheduling as _scheduling
from prefect_orchestration import scratch_loader as _scratch_loader
from prefect_orchestration import serve as _serve
from prefect_orchestration import sessions as _sessions
from prefect_orchestration import spend as _spend
from prefect_orchestration import status as _status
from prefect_orchestration import watch as _watch

app = typer.Typer(
    help="Prefect orchestration for Claude Code agents — pluggable formula runner.",
    no_args_is_help=True,
)


@app.callback()
def _root() -> None:
    """Root — forces Typer to keep subcommand form."""


@app.command(
    "agent",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def agent(
    ctx: typer.Context,
    provider: str = typer.Argument(
        ..., help="Agent provider: claude, codex, or cursor."
    ),
    account: str | None = typer.Option(None, "--account"),
    account_class: str | None = typer.Option(None, "--account-class", "--account-type"),
) -> None:
    """Launch a provider CLI with cwd-aware account isolation."""
    try:
        _account.launch_agent(
            provider,
            list(ctx.args),
            cwd=Path.cwd(),
            account=account,
            account_class=account_class,
        )
    except _account.AccountError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc


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
def list_formulas(
    output_json: bool = typer.Option(False, "--json", help="Output as JSON array."),
) -> None:
    """List formulas + commands registered via `po.formulas` / `po.commands`."""
    import json as _json

    formulas = _load_formulas()
    cmds = _commands.load_commands()
    if not formulas and not cmds:
        if output_json:
            typer.echo("[]")
            return
        typer.echo("no formulas or commands installed.")
        typer.echo("install a pack with `po packs install <pack>`")
        typer.echo(
            'packs declare entries via `[project.entry-points."po.formulas"]` '
            'and `[project.entry-points."po.commands"]`.'
        )
        return

    rows: list[tuple[str, str, str, str]] = []
    for name, flow_obj in formulas.items():
        fn_name = getattr(flow_obj, "__name__", str(flow_obj))
        module = getattr(flow_obj, "__module__", "?")
        doc = (inspect.getdoc(flow_obj) or "").split("\n", 1)[0]
        rows.append(("formula", name, f"{module}:{fn_name}", doc))
    for name, fn in cmds.items():
        fn_name = getattr(fn, "__name__", str(fn))
        module = getattr(fn, "__module__", "?")
        doc = (inspect.getdoc(fn) or "").split("\n", 1)[0]
        rows.append(("command", name, f"{module}:{fn_name}", doc))
    rows.sort(key=lambda r: (r[0], r[1]))

    if output_json:
        typer.echo(
            _json.dumps(
                [
                    {"kind": r[0], "name": r[1], "module": r[2], "doc": r[3]}
                    for r in rows
                ]
            )
        )
        return

    headers = ("KIND", "NAME", "MODULE:CALLABLE", "DOC")
    widths = [
        max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers[:-1])
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths) + "  {}"
    typer.echo(fmt.format(*headers))
    typer.echo(fmt.format(*("-" * w for w in widths), "---"))
    for row in rows:
        typer.echo(fmt.format(*row))


@app.command()
def show(
    name: str,
    output_json: bool = typer.Option(False, "--json", help="Output as JSON object."),
) -> None:
    """Show the signature + docstring of a registered formula or command."""
    import json as _json

    formulas = _load_formulas()
    if name in formulas:
        flow_obj = formulas[name]
        fn = getattr(flow_obj, "fn", flow_obj)
        sig_str = str(inspect.signature(fn))
        doc = inspect.getdoc(fn) or ""
        if output_json:
            typer.echo(
                _json.dumps(
                    {
                        "kind": "formula",
                        "name": name,
                        "module": flow_obj.__module__,
                        "callable": flow_obj.__name__,
                        "signature": sig_str,
                        "doc": doc,
                    }
                )
            )
            return
        typer.echo(f"{name} (formula) — {flow_obj.__module__}:{flow_obj.__name__}")
        typer.echo(f"\nSignature:\n  {fn.__name__}{sig_str}")
        if doc:
            typer.echo(f"\nDoc:\n{doc}")
        return

    cmds = _commands.load_commands()
    if name in cmds:
        fn = cmds[name]
        module = getattr(fn, "__module__", "?")
        fn_name = getattr(fn, "__name__", str(fn))
        try:
            sig_str = str(inspect.signature(fn))
        except (TypeError, ValueError):
            sig_str = "<unavailable>"
        doc = inspect.getdoc(fn) or ""
        if output_json:
            typer.echo(
                _json.dumps(
                    {
                        "kind": "command",
                        "name": name,
                        "module": module,
                        "callable": fn_name,
                        "signature": sig_str,
                        "doc": doc,
                    }
                )
            )
            return
        typer.echo(f"{name} (command) — {module}:{fn_name}")
        typer.echo(f"\nSignature:\n  {fn_name}{sig_str}")
        if doc:
            typer.echo(f"\nDoc:\n{doc}")
        return

    typer.echo(f"no formula or command named {name!r}. Run `po list`.", err=True)
    raise typer.Exit(1)


_DEFAULT_PREFECT_API = "http://127.0.0.1:4200/api"

_DISPATCH_BEAD_KEYS: tuple[str, ...] = ("issue_id", "epic_id", "root_id")


class DispatchTrackerMismatch(ValueError):
    """The caller bead cannot be resolved from the worker's rig tracker."""


def _nearest_tracker_root(start: Path) -> Path | None:
    """Return the nearest ancestor containing ``.beads``.

    Beads resolves a tracker relative to the shellout's working directory.
    Mirroring that ancestor lookup makes the diagnostic useful for both a
    normal single-repo rig and a polyrepo rig whose code lives below the
    tracker root.
    """
    current = start.expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".beads").is_dir():
            return candidate
    return None


def _validate_dispatch_tracker(
    parameters: dict[str, Any], *, caller_path: Path | None = None
) -> None:
    """Reject a dispatch that would strand its seed in another tracker.

    Only the proven mismatch is rejected: the requested bead exists in the
    caller tracker, the caller and rig resolve different tracker roots, and
    the bead is absent from the exact tracker selected by ``rig_path``.
    Missing beads in both trackers retain the formula's existing error path,
    while a nested code path that resolves to the caller tracker remains a
    valid polyrepo rig.
    """
    rig_value = parameters.get("rig_path")
    if not isinstance(rig_value, (str, Path)):
        return

    target_key = next(
        (
            key
            for key in _DISPATCH_BEAD_KEYS
            if isinstance(parameters.get(key), str) and parameters[key]
        ),
        None,
    )
    if target_key is None:
        return

    caller = (caller_path or Path.cwd()).expanduser().resolve()
    rig_path = Path(rig_value).expanduser().resolve()
    caller_root = _nearest_tracker_root(caller)
    rig_root = _nearest_tracker_root(rig_path)
    if caller_root is None or caller_root == rig_root:
        return

    bead_id = parameters[target_key]
    if _beads_meta._bd_show(bead_id, rig_path=caller) is None:
        return
    rig_row = (
        _beads_meta._bd_show(bead_id, rig_path=rig_path) if rig_path.is_dir() else None
    )
    if rig_row is not None:
        return

    caller_tracker = caller_root / ".beads"
    rig_tracker = rig_root / ".beads" if rig_root is not None else "(none found)"
    raise DispatchTrackerMismatch(
        f"dispatch tracker mismatch for {target_key} {bead_id!r}:\n"
        f"  caller tracker:     {caller_tracker}\n"
        f"  --rig-path tracker: {rig_tracker}\n"
        f"  --rig-path:         {rig_path}\n"
        f"{bead_id!r} exists in the caller tracker but not in the tracker the "
        "worker would use. Pass the tracker root as --rig-path (it may be a "
        "polyrepo root), or move/link the bead into the intended rig tracker. "
        "No Prefect flow was submitted."
    )


def _validate_dispatch_tracker_or_exit(
    parameters: dict[str, Any], *, caller_path: Path | None = None
) -> None:
    try:
        _validate_dispatch_tracker(parameters, caller_path=caller_path)
    except DispatchTrackerMismatch as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc


def _autoconfigure_prefect_api() -> None:
    """If a local Prefect server is reachable and PREFECT_API_URL is unset,
    point at it. Avoids the per-`po run` ephemeral-server tax (and the
    socket contention that comes from N concurrent ephemerals fighting
    each other) when the user already has `prefect server start` going.

    Side-effect: mutates `os.environ`, so subprocesses spawned by
    flow tasks (the agent → pytest chain) inherit the same setting.
    """
    if os.environ.get("PREFECT_API_URL"):
        return
    try:
        import urllib.request

        with urllib.request.urlopen(
            f"{_DEFAULT_PREFECT_API}/health", timeout=1.5
        ) as resp:
            if resp.status != 200:
                return
    except Exception:
        return
    os.environ["PREFECT_API_URL"] = _DEFAULT_PREFECT_API
    typer.echo(
        f"[po] PREFECT_API_URL was unset; using local server at {_DEFAULT_PREFECT_API}",
        err=True,
    )


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def run(
    ctx: typer.Context,
    from_file: Path | None = typer.Option(
        None,
        "--from-file",
        help="Path to a .py file containing a @flow function. Skips the "
        "po.formulas entry-point lookup — useful for ad-hoc scratch flows.",
    ),
    flow_name: str | None = typer.Option(
        None,
        "--name",
        help="When --from-file defines multiple @flow callables, pick this one.",
    ),
    when: str | None = typer.Option(
        None,
        "--at",
        help="Schedule the formula's <name>-manual deployment for a future "
        "time instead of running synchronously. Relative duration "
        "(2h, 30m, 1d, +30m) or ISO-8601 with timezone "
        "(2026-04-25T09:00:00-04:00 / 2026-04-25T13:00:00Z). "
        "Auto-applies <formula>-manual if not on the server; CLI warns "
        "when no workers are running on the target pool.",
    ),
    foreground: bool = typer.Option(
        False,
        "--foreground",
        help="Run in this shell instead of submitting to the durable Prefect worker.",
    ),
    time_compat: str | None = typer.Option(
        None,
        "--time",
        hidden=True,
        help="Deprecated alias for --at.",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Provider model alias or full id (for example sonnet, gpt-5.4, "
        "gpt-5.5, or composer-2.5). "
        "Stamps PO_MODEL_CLI; per-role agents/<role>/config.toml beats this.",
    ),
    effort: str | None = typer.Option(
        None,
        "--effort",
        help="Reasoning effort (low|medium|high|xhigh|max). "
        "Stamps PO_EFFORT_CLI; per-role config.toml beats this.",
    ),
    backend: str | None = typer.Option(
        None,
        "--backend",
        help="Explicit runtime backend: cli|tmux|codex-cli|codex-tmux|"
        "cursor-cli|cursor-tmux|stub. Stamps PO_BACKEND.",
    ),
    start_command: str | None = typer.Option(
        None,
        "--start-command",
        help="Override the claude CLI invocation prefix (default: "
        "'claude --dangerously-skip-permissions'). Stamps PO_START_COMMAND_CLI.",
    ),
    account: str | None = typer.Option(
        None,
        "--account",
        help="Explicit coding-account handle for agent subprocesses.",
    ),
    account_class: str | None = typer.Option(
        None,
        "--account-class",
        help="Coding-account class (for example personal or work).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print what would run (formula, kwargs) and exit 0. "
        "No Prefect flow run, no bd writes, no filesystem side effects.",
    ),
    param: list[str] = typer.Option(
        [],
        "--param",
        help="Pass a formula kwarg as key=value, bypassing reserved po-CLI "
        "flags. Use to set a formula's own dry_run/execute (e.g. "
        "--param dry_run=false --param execute=true) since bare --dry-run "
        "is reserved by po. Repeatable; takes precedence over positional extras.",
    ),
    stub_backend: bool = typer.Option(
        False,
        "--stub-backend",
        help="Run the full formula with StubBackend (fake agent turns). "
        "Formerly the --dry-run behavior. Full bd side effects apply.",
    ),
    env_name: str | None = typer.Option(
        None,
        "--env",
        help="Env name to dispatch against (see `po env list`). Use 'up' with --driver "
        "to provision an ephemeral env, run, and tear it down automatically.",
    ),
    rebuild: bool = typer.Option(
        False,
        "--rebuild",
        help="Force rebuild + re-provision of the env before dispatch (requires --env).",
    ),
    env_driver: str | None = typer.Option(
        None,
        "--driver",
        help="Driver for ephemeral env provisioning (use with --env up).",
    ),
    auto_down: str = typer.Option(
        "30m",
        "--auto-down",
        help="Grace window before ephemeral env teardown (e.g. 30m, 1h, 0). Requires --env up.",
    ),
    auto_down_on_failure: bool = typer.Option(
        False,
        "--auto-down-on-failure",
        help="Tear down ephemeral env even if the flow fails. Requires --env up.",
    ),
) -> None:
    """Run a registered formula or an ad-hoc scratch flow.

    Registered:  po run software-dev-full --issue-id sr-8yu.3 --rig site --rig-path ./site
    Scheduled:   po run software-dev-full --at 2h --issue-id ...
    Scratch:     po run --from-file ./my_flow.py [--name foo] --arg value
    Env-backed:  po run software-dev-full --env myenv --issue-id ...
    """
    if time_compat is not None:
        if when is not None:
            typer.echo("error: --at and --time are mutually exclusive", err=True)
            raise typer.Exit(2)
        typer.echo("warning: --time is deprecated; use --at instead", err=True)
        when = time_compat

    if dry_run and stub_backend:
        typer.echo(
            "error: --dry-run and --stub-backend are mutually exclusive", err=True
        )
        raise typer.Exit(2)

    if backend is not None:
        allowed_backends = {
            "cli",
            "tmux",
            "tmux-stream",
            "codex-cli",
            "codex-tmux",
            "codex-tmux-stream",
            "cursor-cli",
            "cursor-tmux",
            "stub",
        }
        if backend not in allowed_backends:
            typer.echo(
                f"error: unknown backend {backend!r}; accepted: "
                f"{', '.join(sorted(allowed_backends))}",
                err=True,
            )
            raise typer.Exit(2)
        os.environ["PO_BACKEND"] = backend

    if account is not None:
        os.environ["PO_ACCOUNT"] = account
    if account_class is not None:
        os.environ["PO_ACCOUNT_CLASS"] = account_class

    _autoconfigure_prefect_api()

    # Formula name is the first non-option token in extras (when --from-file
    # is absent). Extracting manually rather than via typer.Argument lets us
    # coexist with `ignore_unknown_options` — otherwise Click would consume
    # the next unknown `--key` token as the positional name.
    extras = list(ctx.args)
    name: str | None = None
    if extras and not extras[0].startswith("-"):
        name = extras.pop(0)

    if from_file is not None and name is not None:
        typer.echo("specify either a formula name or --from-file, not both.", err=True)
        raise typer.Exit(2)
    if from_file is None and name is None:
        typer.echo(
            "missing formula name. Run `po list`, or use --from-file <path>.",
            err=True,
        )
        raise typer.Exit(2)

    durable_now = (
        when is None
        and not foreground
        and from_file is None
        and env_name is None
        and not dry_run
        and not stub_backend
    )
    if when is not None or durable_now:
        _run_scheduled(
            name=name,
            from_file=from_file,
            when=when,
            extras=extras,
            param=param,
            model=model,
            effort=effort,
            backend=backend,
            start_command=start_command,
            account=account,
            account_class=account_class,
        )
        return

    if from_file is not None:
        try:
            flow_obj = _scratch_loader.load_flow_from_file(from_file, flow_name)
        except _scratch_loader.ScratchLoadError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(2) from exc
        label = f"--from-file {from_file}"
    else:
        assert name is not None
        formulas = _load_formulas()
        if name not in formulas:
            typer.echo(f"no formula named {name!r}. Run `po list`.", err=True)
            raise typer.Exit(1)
        flow_obj = formulas[name]
        label = name

    kwargs = _parse_kwargs(extras)
    _apply_runtime_overrides(
        flow_obj,
        kwargs,
        model=model,
        effort=effort,
        start_command=start_command,
    )
    _merge_param_overrides(kwargs, param)

    if from_file is None and env_name is not None and not dry_run:
        _validate_dispatch_tracker_or_exit(kwargs)

    if env_name == "up":
        if env_driver is None:
            typer.echo("error: --env up requires --driver <name>", err=True)
            raise typer.Exit(2)
        from prefect_orchestration import env_dispatch as _env_dispatch
        from prefect_orchestration.env import _parse_duration

        _env_dispatch.run_ephemeral_env(
            driver_name=env_driver,
            formula=name or "",
            kwargs=kwargs,
            auto_down_secs=_parse_duration(auto_down),
            auto_down_on_failure=auto_down_on_failure,
            issue_id=kwargs.get("issue_id"),
            rig_path=Path(kwargs["rig_path"]) if "rig_path" in kwargs else None,
            rebuild=rebuild,
        )
        return
    elif env_name is not None:
        from prefect_orchestration import env_dispatch as _env_dispatch

        _env_dispatch.run_with_env(
            env_name=env_name,
            formula=name or "",
            kwargs=kwargs,
            rebuild=rebuild,
            issue_id=kwargs.get("issue_id"),
            rig_path=Path(kwargs["rig_path"]) if "rig_path" in kwargs else None,
        )
        return

    if dry_run:
        _print_dry_run_dag(name=name, kwargs=kwargs)
        raise typer.Exit(0)

    if stub_backend:
        os.environ["PO_BACKEND"] = "stub"

    # Foreground interruption is not cancellation. Preserve detached role
    # sessions and their checkpoints so `po resume` can continue them. Only an
    # explicit cancellation command may destroy agent sessions.
    import signal

    def _signal_cleanup(signum: int, _frame: Any) -> None:
        typer.echo(
            f"[po] interrupted by signal {signum}; role sessions preserved for resume",
            err=True,
        )
        # 128 + signum — conventional exit code for signal-terminated.
        raise typer.Exit(128 + signum)

    prior_int = signal.signal(signal.SIGINT, _signal_cleanup)
    prior_term = signal.signal(signal.SIGTERM, _signal_cleanup)
    call_kwargs = _filter_kwargs_for_flow(flow_obj, kwargs, label=label)
    if from_file is None:
        _validate_dispatch_tracker_or_exit(call_kwargs)
    try:
        result = flow_obj(**call_kwargs)
    except TypeError as exc:
        typer.echo(f"bad arguments for {label}: {exc}", err=True)
        if from_file is None:
            typer.echo(f"run `po show {label}` to see the signature", err=True)
        raise typer.Exit(2) from exc
    finally:
        signal.signal(signal.SIGINT, prior_int)
        signal.signal(signal.SIGTERM, prior_term)
        # Write formula stamp so `po retry` can detect the original formula.
        if name is not None and isinstance(kwargs.get("issue_id"), str):
            try:
                loc = _run_lookup.resolve_run_dir(kwargs["issue_id"])
                (loc.run_dir / _retry.FORMULA_STAMP).write_text(name)
            except Exception:  # noqa: BLE001
                pass
    typer.echo(result)


_RUNTIME_KNOBS: tuple[tuple[str, str], ...] = (
    ("model", "PO_MODEL_CLI"),
    ("effort", "PO_EFFORT_CLI"),
    ("start_command", "PO_START_COMMAND_CLI"),
)

_SCHEDULED_RUNTIME_ENV_KEYS: tuple[str, ...] = (
    "PO_BACKEND",
    "PO_ACCOUNT",
    "PO_ACCOUNT_CLASS",
    "PO_MODEL_CLI",
    "PO_EFFORT_CLI",
    "PO_START_COMMAND_CLI",
    "PO_MODEL",
    "PO_EFFORT",
    "PO_START_COMMAND",
)


def _print_dry_run_dag(name: str | None, kwargs: dict[str, Any]) -> None:
    """Print a no-effect summary of what `po run` would do."""
    typer.echo("[dry-run] po run (no Prefect flow run, no bd writes)")
    typer.echo(f"  formula:  {name or '(ad-hoc --from-file)'}")
    for k, v in kwargs.items():
        typer.echo(f"  {k}: {v}")


def _apply_runtime_overrides(
    flow_obj: Any,
    kwargs: dict[str, Any],
    *,
    model: str | None,
    effort: str | None,
    start_command: str | None,
) -> None:
    """Stamp `PO_*_CLI` env vars and pass through to flow kwargs when accepted.

    Per-role config.toml > CLI flag (this layer) > shell env > default.
    Distinct env-var name (`PO_MODEL_CLI` vs `PO_MODEL`) preserves the
    flag-vs-shell precedence in `role_config.resolve_role_runtime`.
    """
    values = {"model": model, "effort": effort, "start_command": start_command}
    flow_params: set[str] = set()
    if flow_obj is not None:
        flow_fn = getattr(flow_obj, "fn", flow_obj)
        try:
            flow_params = set(inspect.signature(flow_fn).parameters.keys())
        except (TypeError, ValueError):
            flow_params = set()
    for arg_name, env_name in _RUNTIME_KNOBS:
        val = values[arg_name]
        if val is None:
            continue
        os.environ[env_name] = val
        if arg_name in flow_params:
            kwargs.setdefault(arg_name, val)


def _scheduled_runtime_job_variables() -> dict[str, Any] | None:
    """Return runtime env that must survive handoff to a scheduled worker."""
    env = {
        key: value
        for key in _SCHEDULED_RUNTIME_ENV_KEYS
        if (value := os.environ.get(key)) is not None
    }
    if not env:
        return None
    return {"env": env}


def _stamp_dispatch_manifest(formula: str, parameters: dict[str, Any]) -> None:
    """Persist the runtime tuple before a worker can claim the flow run."""
    issue_id = parameters.get("issue_id")
    rig_path = parameters.get("rig_path")
    if not isinstance(issue_id, str) or not isinstance(rig_path, str):
        return

    run_dir = Path(rig_path).resolve() / ".planning" / formula / issue_id
    run_dir.mkdir(parents=True, exist_ok=True)
    runtime_env = {
        key: value
        for key in _SCHEDULED_RUNTIME_ENV_KEYS
        if (value := os.environ.get(key)) is not None
    }
    manifest = {
        "formula": formula,
        "issue_id": issue_id,
        "rig_path": str(Path(rig_path).resolve()),
        "parameters": parameters,
        "runtime_env": runtime_env,
        "argv": sys.argv,
    }
    (run_dir / ".po-dispatch.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n"
    )
    (run_dir / _retry.FORMULA_STAMP).write_text(formula)


def _merge_param_overrides(kwargs: dict[str, Any], param: list[str] | None) -> None:
    """Merge `--param key=value` overrides into `kwargs` (in place).

    A `--param` value takes precedence over a same-named positional extra.
    This is the non-colliding way to set a formula's own kwargs whose names
    clash with a reserved `po run` flag — notably `dry_run` (the bare
    `--dry-run` is reserved for po's print-and-exit), but also any future
    formula param that shadows a po-CLI option. Values run through the same
    `_coerce` light-typing as `--key value` extras.
    """
    for item in param or []:
        if "=" not in item:
            raise typer.BadParameter(
                f"--param expects key=value, got {item!r}",
            )
        key, value = item.split("=", 1)
        kwargs[key.strip().replace("-", "_")] = _coerce(value)


def _filter_kwargs_for_flow(
    flow_obj: Any, kwargs: dict[str, Any], *, label: str
) -> dict[str, Any]:
    """Drop kwargs the target formula's signature doesn't accept.

    `po run` injects CLI-isms (rig, rig_path, …) that the software-dev
    formulas declare but other formulas (e.g. provision_business) do not.
    Passing them through raises a Prefect SignatureMismatchError on worker
    pickup (scheduled path) or a TypeError (synchronous path). Filtering to
    the flow fn's real parameters lets one `po run` surface dispatch any
    formula. A formula with a `**kwargs` catch-all keeps everything.

    Dropped keys are reported to stderr — never silent — so a real typo in
    a `--key` is visible rather than swallowed.
    """
    flow_fn = getattr(flow_obj, "fn", flow_obj)
    try:
        params = inspect.signature(flow_fn).parameters
    except (TypeError, ValueError):
        return kwargs
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return kwargs
    accepted = set(params)
    dropped = [k for k in kwargs if k not in accepted]
    if dropped:
        typer.echo(
            f"[po] {label}: dropping {len(dropped)} param(s) not in the "
            f"formula signature: {', '.join(sorted(dropped))}",
            err=True,
        )
    return {k: v for k, v in kwargs.items() if k in accepted}


def _run_scheduled(
    *,
    name: str | None,
    from_file: Path | None,
    when: str | None,
    extras: list[str],
    param: list[str] | None = None,
    model: str | None = None,
    effort: str | None = None,
    backend: str | None = None,
    start_command: str | None = None,
    account: str | None = None,
    account_class: str | None = None,
) -> None:
    """Implementation of `po run <formula> --at <when>`.

    Resolves `<formula>-manual` on the Prefect server (per
    engdocs/principles.md §1 — collapses
    `prefect deployment run <flow>/<formula>-manual --start-in 2h`
    into the by-convention shape) and submits a one-shot scheduled
    flow-run with the parsed CLI kwargs as parameters. Auto-applies
    the deployment if absent from the server.
    """
    if from_file is not None:
        typer.echo(
            "--at and --from-file are mutually exclusive: scratch flows "
            "aren't registered as deployments and have no manual deployment "
            "to schedule against.",
            err=True,
        )
        raise typer.Exit(2)
    if name is None:
        typer.echo(
            "--at requires a formula name (no scratch-flow scheduling).",
            err=True,
        )
        raise typer.Exit(2)
    formulas = _load_formulas()
    if name not in formulas:
        typer.echo(f"no formula named {name!r}. Run `po list`.", err=True)
        raise typer.Exit(1)

    if when is None:
        from datetime import datetime, timezone

        scheduled_time = datetime.now(timezone.utc)
    else:
        try:
            scheduled_time = _scheduling.parse_when(when)
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(2) from exc

    kwargs = _parse_kwargs(extras)
    _apply_runtime_overrides(
        formulas[name],
        kwargs,
        model=model,
        effort=effort,
        start_command=start_command,
    )
    if backend is not None:
        os.environ["PO_BACKEND"] = backend
    if account is not None:
        os.environ["PO_ACCOUNT"] = account
    if account_class is not None:
        os.environ["PO_ACCOUNT_CLASS"] = account_class
    _merge_param_overrides(kwargs, param)
    issue_id = kwargs.get("issue_id")
    # Filter to the formula signature so non-software-dev formulas don't get
    # rig/rig_path (etc.) baked into the deployment parameters — those raise
    # SignatureMismatchError when the worker picks the run up.
    parameters = _filter_kwargs_for_flow(formulas[name], kwargs, label=name)
    _validate_dispatch_tracker_or_exit(parameters)
    _stamp_dispatch_manifest(name, parameters)

    import asyncio

    from prefect.client.orchestration import get_client

    async def _go() -> tuple[Any, str]:
        async with get_client() as client:
            return await _scheduling.submit_scheduled_run(
                client=client,
                formula=name,
                parameters=parameters,
                scheduled_time=scheduled_time,
                issue_id=issue_id if isinstance(issue_id, str) else None,
                job_variables=_scheduled_runtime_job_variables(),
            )

    try:
        flow_run, full_name, warn_msg = asyncio.run(_go())
    except _scheduling.ManualDeploymentMissing as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(3) from exc
    except Exception as exc:  # noqa: BLE001 — surface the Prefect failure with a hint
        api = os.environ.get("PREFECT_API_URL", "<unset>")
        typer.echo(
            f"error: could not schedule run via Prefect at {api}: {exc}\n"
            f"  hint: `po serve install` or `prefect server start` "
            f"to bring one up.",
            err=True,
        )
        raise typer.Exit(4) from exc

    typer.echo(
        f"submitted flow-run {flow_run.id} ({full_name}) at {scheduled_time.isoformat()}"
    )
    if warn_msg:
        typer.echo(warn_msg, err=True)
    else:
        # A worker is auto-ensured on the pool (see scheduling._ensure_worker_for_pool),
        # so no manual `prefect worker start` reminder here.
        typer.echo(
            "queued on the durable worker." if when is None else f"queued for {when}."
        )


@app.command()
def deploy(
    apply: bool = typer.Option(
        False, "--apply", help="Create/update deployments on the Prefect server."
    ),
    pack: str | None = typer.Option(None, "--pack", help="Only include this pack."),
    name: str | None = typer.Option(
        None, "--name", help="Only include this deployment name."
    ),
    work_pool: str | None = typer.Option(
        None,
        "--work-pool",
        help="Assign this work pool to each deployment before apply.",
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
            'packs declare deployments via `[project.entry-points."po.deployments"]` '
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


cron_app = typer.Typer(
    no_args_is_help=True,
    help="Apply / list recurring cron deployments from a directory of *.toml orders.",
)


@cron_app.command("apply")
def cron_apply(
    orders_dir: Path = typer.Option(
        ...,
        "--orders-dir",
        help="Directory of flat *.toml order files (cron/formula/params/tags/timezone).",
    ),
    work_pool: str = typer.Option(
        "po", "--work-pool", help="Work pool to pin each cron deployment to."
    ),
    tag_prefix: str = typer.Option(
        "po-cron",
        "--tag-prefix",
        help="Tag prefix applied to deployments lacking an explicit tags list.",
    ),
    default_timezone: str = typer.Option(
        "UTC",
        "--default-timezone",
        help="Timezone for orders that omit a timezone key.",
    ),
) -> None:
    """Build cron deployments from `--orders-dir` and apply them to Prefect."""
    if not orders_dir.is_dir():
        typer.echo(f"orders dir not found: {orders_dir}", err=True)
        raise typer.Exit(2)

    deployments = _deployments.build_cron_deployments_from_order_dir(
        orders_dir,
        tag_prefix=tag_prefix,
        default_timezone=default_timezone,
        work_pool_name=work_pool,
    )
    if not deployments:
        typer.echo(f"no cron deployments built from {orders_dir} (no valid *.toml).")
        raise typer.Exit(1)

    if not _deployments.prefect_api_configured():
        typer.echo(
            "PREFECT_API_URL is not set — point it at a running Prefect server "
            "(e.g. `prefect server start` → http://127.0.0.1:4200/api).",
            err=True,
        )
        raise typer.Exit(2)

    failures = 0
    for deployment in deployments:
        label = getattr(deployment, "name", "?")
        try:
            dep_id = _deployments.apply_deployment(deployment, work_pool=work_pool)
        except Exception as exc:
            typer.echo(f"  FAIL  {label}  ({exc})", err=True)
            failures += 1
            continue
        typer.echo(f"  OK    {label}  → {dep_id}")
    if failures:
        raise typer.Exit(1)


@cron_app.command("list")
def cron_list(
    orders_dir: Path = typer.Option(
        ...,
        "--orders-dir",
        help="Directory of flat *.toml order files (cron/formula/params/tags/timezone).",
    ),
    work_pool: str = typer.Option(
        "po", "--work-pool", help="Work pool the cron deployments would pin to."
    ),
    tag_prefix: str = typer.Option(
        "po-cron",
        "--tag-prefix",
        help="Tag prefix applied to deployments lacking an explicit tags list.",
    ),
    default_timezone: str = typer.Option(
        "UTC",
        "--default-timezone",
        help="Timezone for orders that omit a timezone key.",
    ),
) -> None:
    """List cron deployments declared by `--orders-dir` without applying them.

    Cross-checks against server state when a Prefect API is reachable so you
    can see which declared deployments are already live.
    """
    if not orders_dir.is_dir():
        typer.echo(f"orders dir not found: {orders_dir}", err=True)
        raise typer.Exit(2)

    deployments = _deployments.build_cron_deployments_from_order_dir(
        orders_dir,
        tag_prefix=tag_prefix,
        default_timezone=default_timezone,
        work_pool_name=work_pool,
    )
    if not deployments:
        typer.echo(f"no cron deployments built from {orders_dir} (no valid *.toml).")
        raise typer.Exit(1)

    live_names = _cron_live_deployment_names()

    rows = []
    for deployment in deployments:
        name = getattr(deployment, "name", "?")
        flow_name = getattr(deployment, "flow_name", "?")
        if live_names is None:
            status = "?"
        else:
            status = "live" if f"{flow_name}/{name}" in live_names else "declared"
        rows.append(
            (
                name,
                flow_name,
                _deployments.format_schedule(deployment),
                status,
            )
        )
    headers = ("DEPLOYMENT", "FLOW", "SCHEDULE", "SERVER")
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    typer.echo(fmt.format(*headers))
    typer.echo(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        typer.echo(fmt.format(*row))


def _cron_live_deployment_names() -> set[str] | None:
    """Return `{flow_name/deployment_name}` for deployments live on the server.

    Returns None when no Prefect API is configured or the server is
    unreachable, so callers can render a `?` instead of a misleading
    `declared`.
    """
    if not _deployments.prefect_api_configured():
        return None
    try:
        from prefect.client.orchestration import get_client

        async def _fetch() -> set[str]:
            async with get_client() as client:
                deps = await client.read_deployments()
                return {f"{d.flow_name or '?'}/{d.name}" for d in deps}

        import anyio

        return anyio.run(_fetch)
    except Exception:  # noqa: BLE001 — server unreachable is non-fatal for `list`
        return None


@app.command()
def logs(
    issue_id: str = typer.Argument(
        ..., help="Beads issue id (e.g. prefect-orchestration-5i9)"
    ),
    lines: int = typer.Option(200, "-n", "--lines", help="Tail this many lines."),
    follow: bool = typer.Option(
        False, "-f", "--follow", help="Stream new lines (execs `tail -F`)."
    ),
    file: str | None = typer.Option(
        None, "--file", help="Override auto-pick: filename relative to run_dir."
    ),
) -> None:
    """Tail the freshest log artifact for a beads issue's run_dir."""
    try:
        loc = _run_lookup.resolve_run_dir(issue_id)
    except _run_lookup.RunDirNotFound as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    if file is not None:
        target = loc.run_dir / file
        if not target.exists():
            typer.echo(f"no such file: {target}", err=True)
            raise typer.Exit(3)
    else:
        target = _run_lookup.pick_freshest(_run_lookup.candidate_log_files(loc))
        if target is None:
            typer.echo(
                f"no log files found under {loc.run_dir}. "
                "Either the run hasn't produced logs yet, or they live outside "
                "the known patterns (try --file <name>).",
                err=True,
            )
            raise typer.Exit(4)

    if follow:
        # exec so Ctrl-C, signals, and tail's own buffering all behave
        # naturally. POSIX-only — acceptable (Prefect is POSIX-only).
        os.execvp("tail", ["tail", "-n", str(lines), "-F", str(target)])

    try:
        rel = target.relative_to(loc.run_dir)
        header = f"===== {rel} ====="
    except ValueError:
        header = f"===== {target} ====="
    typer.echo(header)
    _print_tail(target, lines)


def _print_tail(path: Path, lines: int) -> None:
    """Python tail-N, avoiding a full-file read for large logs."""
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        block = 8192
        data = b""
        while size > 0 and data.count(b"\n") <= lines:
            step = min(block, size)
            size -= step
            f.seek(size)
            data = f.read(step) + data
    text = data.decode("utf-8", errors="replace")
    tail = text.splitlines()[-lines:]
    for line in tail:
        typer.echo(line)


@app.command()
def artifacts(
    issue_id: str = typer.Argument(
        ..., help="Beads issue id (e.g. prefect-orchestration-5i9)"
    ),
    verdicts: bool = typer.Option(
        False,
        "--verdicts",
        help="Print only the bd-metadata verdicts (po.* keys on iter beads).",
    ),
    open_: bool = typer.Option(
        False,
        "--open",
        help=(
            "Launch $EDITOR (or xdg-open) on the run dir instead of printing. "
            "Takes precedence over --verdicts."
        ),
    ),
) -> None:
    """Dump the full forensic trail for a beads issue's run dir.

    Prints triage.md, plan.md, each critique-iter-N / verification-report-iter-N
    pair in numeric N order, decision-log.md, lessons-learned.md, and every
    `po.*` metadata key found on iter beads under the seed. Missing files
    render as `(missing)` — the command never aborts on a partial run.
    """
    try:
        loc = _run_lookup.resolve_run_dir(issue_id)
    except _run_lookup.RunDirNotFound as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    if open_:
        editor = os.environ.get("EDITOR") or shutil.which("xdg-open")
        if not editor:
            typer.echo(
                "no $EDITOR set and `xdg-open` not on PATH; cannot --open.",
                err=True,
            )
            raise typer.Exit(5)
        proc = subprocess.run([editor, str(loc.run_dir)], check=False)
        if proc.returncode != 0:
            typer.echo(
                f"{editor!r} exited {proc.returncode}; on bare servers xdg-open "
                "often fails — try EDITOR=vim or cd to the dir directly.",
                err=True,
            )
            raise typer.Exit(proc.returncode)
        return

    sections = _artifacts.collect_sections(loc.run_dir, verdicts_only=verdicts)
    typer.echo(_artifacts.render(sections))
    typer.echo(f"\nfor full trace: po trace {issue_id}")


@app.command()
def trace(
    issue_id: str = typer.Argument(..., help="Beads issue id"),
    role: str | None = typer.Option(
        None, "--role", help="Full transcript for one role"
    ),
    tools: bool = typer.Option(
        False, "--tools", help="Chronological tool-call timeline"
    ),
    tokens: bool = typer.Option(
        False, "--tokens", help="Token + cache breakdown table"
    ),
    turn: int | None = typer.Option(
        None, "--turn", help="Show one specific turn (requires --role)"
    ),
    slow: float | None = typer.Option(
        None, "--slow", help="Show turns slower than N seconds"
    ),
    output_json: bool = typer.Option(
        False, "--json", help="Raw JSON output for piping"
    ),
) -> None:
    """Inspect agent traces for an issue's PO run.

    Default: per-role summary table (ROLE/MODEL/TURNS/TOOLS/IN_TOK/OUT_TOK/CACHE_R/THINK/WALL).
    --role <r>: full per-turn transcript.
    --tools: chronological tool-call timeline across all roles.
    --tokens: token + cache breakdown table.
    --turn N: single turn detail (requires --role).
    --slow N: turns that took longer than N seconds.
    --json: machine-readable JSON array.
    """
    try:
        loc = _run_lookup.resolve_run_dir(issue_id)
    except _run_lookup.RunDirNotFound as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    from prefect_orchestration import beads_meta as _beads_meta

    try:
        seed_id = _beads_meta.resolve_seed_bead(issue_id, rig_path=loc.rig_path)
    except ValueError:
        seed_id = issue_id
    seed_run_dir = loc.run_dir
    if seed_id != issue_id:
        formula_dir = loc.run_dir.parent
        candidate = formula_dir / seed_id
        if candidate.is_dir():
            seed_run_dir = candidate

    metadata = _sessions.load_role_sessions(
        loc.run_dir,
        seed_id=seed_id,
        seed_run_dir=seed_run_dir,
        rig_path=loc.rig_path,
    )

    if not metadata:
        typer.echo(
            f"no session metadata recorded for {issue_id}. "
            "The flow may not have completed the session-stamping step yet.",
            err=True,
        )
        raise typer.Exit(3)

    traces: list[_trace.RoleTrace] = []
    missing: list[str] = []
    for key, uuid in sorted(metadata.items()):
        if not key.startswith("session_"):
            continue
        role_name = key[len("session_") :]
        jsonl_path = _trace.find_jsonl(uuid, loc.rig_path)
        if jsonl_path is None:
            missing.append(f"  {role_name}: JSONL not found (uuid={uuid})")
            turns: list[_trace.TurnRecord] = []
        else:
            turns = _trace.parse_jsonl(jsonl_path)
        traces.append(
            _trace.RoleTrace(
                role=role_name, uuid=uuid, turns=turns, jsonl_path=jsonl_path
            )
        )

    for warn_line in missing:
        typer.echo(warn_line, err=True)

    if not traces:
        typer.echo("no roles found in session metadata.", err=True)
        raise typer.Exit(4)

    import json as _json

    if output_json:
        typer.echo(_json.dumps(_trace.to_json_list(traces), indent=2))
        return

    if turn is not None:
        if role is None:
            typer.echo("--turn requires --role", err=True)
            raise typer.Exit(2)
        typer.echo(_trace.format_turn_detail(traces, role, turn))
        return

    if slow is not None:
        typer.echo(_trace.format_slow_turns(traces, slow))
        return

    if tools:
        typer.echo(_trace.format_tools_timeline(traces))
        return

    if tokens:
        summaries = _trace.summarize(traces)
        typer.echo(_trace.format_tokens_table(summaries))
        return

    if role is not None:
        typer.echo(_trace.format_transcript(traces, role))
        return

    summaries = _trace.summarize(traces)
    typer.echo(_trace.format_summary_table(summaries))


@app.command()
def doctor(
    check: str | None = typer.Option(
        None, "--check", help="Run only a named check ('locks', 'envs', or 'cron')."
    ),
    fix: bool = typer.Option(
        False, "--fix", help="For --check=locks: delete stale lock files."
    ),
    orders_dir: Path = typer.Option(
        Path(".po-cron"),
        "--orders-dir",
        help="For --check=cron: directory of flat *.toml cron orders.",
    ),
) -> None:
    """Read-only health check of the full PO wiring.

    Runs core checks (`bd` CLI, Prefect server reachability, at least one
    work pool, formula + deployment entry points load, uv-tool install
    freshness, LOGFIRE telemetry token), then any checks contributed by
    installed packs via the `po.doctor_checks` entry-point group. Each
    pack check is wrapped in a 5s soft timeout (yellow on timeout). Exits
    1 if any check is red; warnings never affect the exit code.

    Pass --check=locks to scan for stale .retry.lock files only.
    Add --fix to delete any stale locks found.
    Pass --check=cron --orders-dir <dir> to check declared cron deployments
    against server state and pool-worker health.
    """
    if check == "locks":
        result = _doctor.check_stale_locks()
        report = _doctor.DoctorReport(results=[result])
        typer.echo(_doctor.render_table(report))
        if fix:
            removed = _doctor.clean_stale_locks()
            for p in removed:
                typer.echo(f"  removed: {p}", err=True)
            if removed:
                typer.echo(f"Removed {len(removed)} stale lock(s).", err=True)
        raise typer.Exit(0)
    elif check == "envs":
        results = _doctor.run_env_checks()
        report = _doctor.DoctorReport(results=results)
        typer.echo(_doctor.render_table(report))
        raise typer.Exit(report.exit_code)
    elif check == "cron":
        results = _doctor.run_cron_checks(orders_dir)
        report = _doctor.DoctorReport(results=results)
        typer.echo(_doctor.render_table(report))
        raise typer.Exit(report.exit_code)
    elif check is not None:
        typer.echo(
            f"Unknown --check value: {check!r}. Supported: 'locks', 'envs', 'cron'",
            err=True,
        )
        raise typer.Exit(1)
    else:
        report = _doctor.run_doctor()
        typer.echo(_doctor.render_table(report))
        raise typer.Exit(report.exit_code)


@app.command()
def status(
    issue_id: str | None = typer.Option(
        None, "--issue-id", help="Filter to runs tagged `issue_id:<id>`."
    ),
    since: str | None = typer.Option(
        None, "--since", help="Relative (1h, 30m, 2d) or ISO-8601. Default: 24h."
    ),
    all_: bool = typer.Option(
        False, "--all", help="Ignore default `--since` window and show everything."
    ),
    state: str | None = typer.Option(
        None, "--state", help="Filter by Prefect state name (Running, Completed, ...)."
    ),
    limit: int = typer.Option(
        200,
        "--limit",
        help="Max recent flow runs to fetch; non-terminal runs are always included.",
    ),
    include_zombies: bool = typer.Option(
        False,
        "--include-zombies",
        help="Show 'Running' flows whose rig_path no longer exists on disk "
        "(usually pytest fixtures whose process died before reaching a "
        "terminal state). Hidden by default.",
    ),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON array."),
) -> None:
    """List active / recent flow runs grouped by beads `issue_id` tag.

    `prefect flow-run ls` is unaware of bead IDs. This pulls recent runs
    from the Prefect server, groups by the `issue_id:<id>` tag PO stamps
    onto each run, and prints one row per issue. Always exits 0 — an
    observation command, not a check.

    Zombie filter: a flow whose state is `Running` but whose `rig_path`
    parameter points at a directory that no longer exists is presumed
    dead (the parent process — usually pytest — exited without
    transitioning the flow to a terminal state). These are hidden by
    default to avoid drowning real runs; pass `--include-zombies` to
    show them. Cancelled / Completed / Failed runs are always shown
    regardless of rig_path existence.
    """
    import anyio

    from prefect.client.orchestration import get_client

    since_dt = None
    if not all_:
        spec = since or "24h"
        try:
            since_dt = _status.parse_since(spec)
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            return

    async def _main() -> None:
        try:
            async with get_client() as client:
                runs = await _status.find_runs_by_issue_id(
                    client,
                    issue_id=issue_id,
                    since=since_dt,
                    state=state,
                    limit=limit,
                )
                groups = _status.group_by_issue(runs)
                for g in groups:
                    g.current_step = await _status.current_step_for_flow_run(
                        client, g.latest.id
                    )
                # Compute staleness for RUNNING flows (bd subprocess per run).
                for g in groups:
                    g_state = (getattr(g.latest, "state_name", None) or "").lower()
                    if g_state == "running":
                        params = getattr(g.latest, "parameters", None) or {}
                        rig_path = params.get("rig_path")
                        g.stale_secs = _status.compute_stale_secs(
                            g.issue_id, Path(rig_path) if rig_path else None
                        )
        except Exception as exc:  # noqa: BLE001 — AC3: observation, no tracebacks
            api_url = os.environ.get("PREFECT_API_URL", "<unset>")
            typer.echo(
                f"error: could not query Prefect server at {api_url}: {exc}",
                err=True,
            )
            return
        import json as _json

        if output_json:
            if not include_zombies:
                groups, _ = _status.partition_zombies(groups)
            typer.echo(_json.dumps(_status.to_json_list(groups)))
            return
        if not include_zombies:
            groups, hidden = _status.partition_zombies(groups)
            typer.echo(_status.render_table(groups))
            if hidden:
                typer.echo(
                    f"\n  ({hidden} zombie row(s) hidden — "
                    f"`po status --include-zombies` to show)"
                )
        else:
            typer.echo(_status.render_table(groups))

    anyio.run(_main)


@app.command()
def wait(
    issue_ids: list[str] = typer.Argument(  # noqa: B008
        ..., help="One or more beads issue ids to wait for."
    ),
    any_: bool = typer.Option(
        False,
        "--any",
        help="Exit when ANY of the given issues closes (default: wait for ALL).",
    ),
    timeout: int = typer.Option(
        3600,
        "--timeout",
        "-t",
        help="Maximum seconds to wait before giving up (default: 3600 = 1h).",
    ),
    poll: int = typer.Option(
        30,
        "--poll",
        "-p",
        help="Seconds between bd-show polls (default: 30).",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress per-poll status lines; only print the final summary.",
    ),
) -> None:
    """Block until one or more bd issues reach `closed` state.

    Polls `bd show <id>` every `--poll` seconds. Exits when:

      - default: every id is closed (or one fails / timeout fires)
      - with `--any`: as soon as the first id closes

    Designed to be run with `run_in_background: true` from an agent
    harness — the harness gets a clean exit-code signal instead of
    streaming a watch UI.

    Exit codes:

      0   all (or any, with --any) closed; close-reasons look like success
      1   at least one closed with a failure-coded reason
          (`failed:`, `cap-exhausted`, `nudge failed`, `force-closed`,
          `regression:`, `rejected:`)
      2   timeout reached before terminal state
      3   bd not available / no such issue
    """
    import time as _time

    from prefect_orchestration.beads_meta import _bd_available, _bd_show
    from prefect_orchestration.run_lookup import lookup_prefect_run

    if not _bd_available():
        typer.echo("error: bd not on PATH", err=True)
        raise typer.Exit(3)

    # Resolve each input token to (canonical_id, rig_path) once via
    # Prefect, then poll `bd show canonical_id` in that rig. Tokens can
    # be: bead id, flow-run name, or flow-run UUID prefix (matches what
    # `po status` shows in its ISSUE / FLOW / RUN columns).
    resolved: dict[str, tuple[str, Path | None]] = {}

    def _resolve(token: str) -> tuple[str, Path | None]:
        if token in resolved:
            return resolved[token]
        # Try bd in cwd first — fastest path for the in-rig case.
        row = _bd_show(token)
        if row is not None:
            resolved[token] = (token, None)
            return resolved[token]
        # Fallback: ask Prefect. Returns canonical bead id even when the
        # caller passed a UUID prefix.
        info = lookup_prefect_run(token)
        if info is not None:
            rp, bead_id = info
            resolved[token] = (bead_id, rp)
        else:
            resolved[token] = (token, None)
        return resolved[token]

    def _show(token: str) -> dict | None:
        bead_id, rp = _resolve(token)
        return _bd_show(bead_id, rig_path=rp)

    # Agent close-reasons follow a verb-first convention (`approved: …`,
    # `passed: …`, `no regression: …` for success vs `rejected: …`,
    # `failed: …`, `regression: …` for failure). Match on prefix to
    # avoid false-positives like "no regression:" containing "regression:".
    failure_prefixes = (
        "failed:",
        "cap-exhausted",
        "nudge failed",
        "force-closed",
        "regression:",
        "rejected:",
    )

    def _looks_failed(reason: str) -> bool:
        r = (reason or "").lower().lstrip()
        return any(r.startswith(m) for m in failure_prefixes)

    deadline = _time.monotonic() + max(timeout, 1)
    seen_closed: dict[str, str] = {}  # id → close_reason

    while True:
        # Snapshot all issues each tick (cheap; bd show is local sql).
        for issue_id in issue_ids:
            if issue_id in seen_closed:
                continue
            row = _show(issue_id)
            if row is None:
                typer.echo(
                    f"error: bd show {issue_id!r} returned no row "
                    f"(typo? not initialized? or running in a rig you "
                    f"haven't opened? Prefect lookup also missed.)",
                    err=True,
                )
                raise typer.Exit(3)
            if row.get("status") == "closed":
                # bd show JSON uses `close_reason`; agent_step uses
                # `closure_reason` for in-memory rows. Accept both.
                seen_closed[issue_id] = (
                    row.get("close_reason") or row.get("closure_reason") or ""
                )
                if not quiet:
                    typer.echo(f"✓ closed: {issue_id} — {seen_closed[issue_id]}")

        # Termination conditions.
        if any_ and seen_closed:
            break
        if not any_ and len(seen_closed) == len(issue_ids):
            break
        if _time.monotonic() >= deadline:
            still_open = [i for i in issue_ids if i not in seen_closed]
            typer.echo(
                f"timeout after {timeout}s; still open: {still_open}",
                err=True,
            )
            raise typer.Exit(2)

        if not quiet:
            still_open = [i for i in issue_ids if i not in seen_closed]
            typer.echo(
                f"  waiting on {len(still_open)} of {len(issue_ids)}: {still_open}"
            )
        _time.sleep(max(poll, 1))

    # Summarise + decide exit code.
    failed = [i for i, r in seen_closed.items() if _looks_failed(r)]
    if failed:
        typer.echo(f"⚠ failure-coded close on: {failed}", err=True)
        raise typer.Exit(1)
    typer.echo(f"✓ done ({len(seen_closed)}/{len(issue_ids)} closed cleanly)")


@app.command()
def sessions(
    issue_id: str = typer.Argument(
        ..., help="Beads issue id (e.g. prefect-orchestration-5i9)"
    ),
    resume: str | None = typer.Option(
        None,
        "--resume",
        help="Print a ready-to-run `claude --print --resume <uuid> --fork-session` "
        "one-liner for this role and exit.",
    ),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON array."),
) -> None:
    """List per-role Claude session UUIDs recorded for an issue's run_dir.

    Reads `metadata.json` at the run_dir root (resolved via bead metadata)
    and prints a table of `role | uuid | last-iter | last-updated`. With
    `--resume <role>`, prints a single copy-paste command for that role.
    """
    try:
        loc = _run_lookup.resolve_run_dir(issue_id)
    except _run_lookup.RunDirNotFound as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    # Resolve the seed bead so reads union the seed's role-sessions
    # map (BeadsStore tier + role-sessions.json) with the legacy
    # per-bead metadata.json shim. For solo runs (no parent-child
    # parent), seed == issue_id and behaviour is identical to before.
    from prefect_orchestration import beads_meta as _beads_meta

    try:
        seed_id = _beads_meta.resolve_seed_bead(issue_id, rig_path=loc.rig_path)
    except ValueError:
        seed_id = issue_id
    seed_run_dir = loc.run_dir
    if seed_id != issue_id:
        # Try the canonical layout `<rig>/.planning/<formula>/<seed>/`,
        # using the active issue's run_dir to infer `<formula>`.
        formula_dir = loc.run_dir.parent
        candidate = formula_dir / seed_id
        if candidate.is_dir():
            seed_run_dir = candidate
        # else: keep loc.run_dir; RoleSessionStore.all() degrades to
        # legacy + BeadsStore tiers (json file just won't be found).

    metadata = _sessions.load_role_sessions(
        loc.run_dir,
        seed_id=seed_id,
        seed_run_dir=seed_run_dir,
        rig_path=loc.rig_path,
    )

    if not metadata:
        # Preserve the legacy "metadata.json missing" exit code (3) so
        # scripted callers keep working. We only land here when *all*
        # three tiers are empty — which is genuinely "session info not
        # yet recorded."
        legacy_path = loc.run_dir / _sessions.METADATA_FILENAME
        if not legacy_path.exists():
            typer.echo(
                f"no {_sessions.METADATA_FILENAME} in {loc.run_dir}. "
                "The flow may not have completed the session-stamping step yet.",
                err=True,
            )
            raise typer.Exit(3)

    if resume is not None:
        uuid = _sessions.lookup_session(metadata, resume)
        if uuid is None:
            typer.echo(f"no session recorded for role {resume!r}", err=True)
            raise typer.Exit(4)
        typer.echo(_sessions.resume_command(uuid))
        return

    bead_meta = _attach.fetch_bead_metadata(issue_id)
    pod = bead_meta.get(_attach.META_K8S_POD)
    rows = _sessions.build_rows(loc.run_dir, metadata, pod=pod)
    if output_json:
        import json as _json

        typer.echo(_json.dumps(_sessions.to_json_list(rows)))
        return
    typer.echo(_sessions.render_table(rows))


@app.command()
def retry(
    issue_id: str = typer.Argument(
        ..., help="Beads issue id whose run_dir should be archived + relaunched."
    ),
    keep_sessions: bool = typer.Option(
        False,
        "--keep-sessions",
        help=(
            "Preserve per-role Claude session UUIDs from the prior run's "
            "metadata.json. No-op for sessions stored on the seed bead "
            "(BeadsStore) or in role-sessions.json — those survive the "
            "archive automatically. The legacy-shim path still honours "
            "this flag; the archive's metadata.json is also resurfaced "
            "via the migration shim post-archive."
        ),
    ),
    rig: str | None = typer.Option(
        None,
        "--rig",
        help="Rig name passed to the formula. Defaults to the rig_path basename.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Skip the in-flight check (Prefect Running runs for this issue).",
    ),
    formula: str | None = typer.Option(
        None,
        "--formula",
        help="Formula entry-point name to relaunch. If omitted, po retry reads "
        ".po-formula from the run_dir or falls back to Prefect history.",
    ),
    when: str | None = typer.Option(
        None,
        "--at",
        help="Schedule the retry as a future Prefect flow-run instead of "
        "launching in-process. Same format as `po run --at`: relative "
        "(2h, 30m, 1d) or ISO-8601 with timezone. Auto-applies the "
        "<formula>-manual deployment if absent.",
    ),
    env_name: str | None = typer.Option(
        None,
        "--env",
        help="(stub) Env to dispatch against. Full wiring is a follow-up.",
        hidden=True,
    ),
    rebuild: bool = typer.Option(
        False,
        "--rebuild",
        help="(stub) Force rebuild before dispatch (requires --env).",
        hidden=True,
    ),
) -> None:
    """Archive an issue's run_dir and re-run its formula from scratch.

    Looks up `(rig_path, run_dir)` from bd metadata, archives the
    run_dir to a `.bak-<utc-timestamp>` sibling, reopens the bead if
    closed, and invokes the formula in-process. Refuses to proceed if
    another flow for this issue is still Running on the Prefect server
    (pass `--force` to bypass). Pass `--at <when>` to schedule the
    retry as a future flow-run instead.
    """
    if env_name is not None:
        typer.echo(
            "warning: --env on po retry is not yet wired; use `po run --env` instead.",
            err=True,
        )
    try:
        result = _retry.retry_issue(
            issue_id,
            keep_sessions=keep_sessions,
            rig=rig,
            force=force,
            formula=formula,
            when=when,
            warn=lambda msg: typer.echo(f"warning: {msg}", err=True),
        )
    except _run_lookup.RunDirNotFound as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    except _retry.RetryError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc

    if result.archived_to is not None:
        typer.echo(f"archived → {result.archived_to}")
    else:
        typer.echo("no prior run_dir on disk; launching fresh.")
    if result.reopened:
        typer.echo(f"reopened bead {issue_id}")
    if result.kept_sessions:
        typer.echo("restored metadata.json (--keep-sessions)")
    typer.echo(result.flow_result)


@app.command()
def resume(
    issue_id: str = typer.Argument(
        ..., help="Beads issue id whose flow should be resumed in place."
    ),
    rig: str | None = typer.Option(
        None,
        "--rig",
        help="Rig name passed to the formula. Defaults to the rig_path basename.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Skip the in-flight check (Prefect Running runs for this issue).",
    ),
    formula: str = typer.Option(
        _resume.DEFAULT_FORMULA,
        "--formula",
        help="Formula entry-point name to relaunch.",
    ),
    when: str | None = typer.Option(
        None,
        "--at",
        help=(
            "Schedule the resume as a future Prefect flow-run instead of "
            "launching in-process. Same format as `po run --at`: relative "
            "(2h, 30m, 1d) or ISO-8601 with timezone."
        ),
    ),
    foreground: bool = typer.Option(
        False,
        "--foreground",
        help="Resume in this shell instead of submitting to the durable worker.",
    ),
    env_name: str | None = typer.Option(
        None,
        "--env",
        help="(stub) Env to dispatch against. Full wiring is a follow-up.",
        hidden=True,
    ),
    rebuild: bool = typer.Option(
        False,
        "--rebuild",
        help="(stub) Force rebuild before dispatch (requires --env).",
        hidden=True,
    ),
) -> None:
    """Resume a failed flow without archiving its run_dir.

    Unlike `po retry` (which archives run_dir → `.bak-<utc>` and starts
    fresh from triage), `po resume` preserves the run_dir as-is. Steps
    whose iter bead already carries a `po.<step>` metadata key are
    skipped — the formula's `prompt_for_bead_verdict` short-circuits
    via `PO_RESUME=1` and returns the existing verdict instead of
    re-prompting the agent.

    Use this when a wave wedges deep in the DAG (e.g. on review with
    triage/baseline/plan/build/lint/test verdicts already stamped on
    their iter beads): it picks up at the failing step instead of
    burning 10+ min re-running the upstream successes.
    """
    if env_name is not None:
        typer.echo(
            "warning: --env on po resume is not yet wired; use `po run --env` instead.",
            err=True,
        )
    try:
        result = _resume.resume_issue(
            issue_id,
            rig=rig,
            force=force,
            formula=formula,
            when=when,
            foreground=foreground,
        )
    except _run_lookup.RunDirNotFound as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    except _resume.ResumeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc

    if result.completed_steps:
        typer.echo(
            f"resuming {issue_id} — {len(result.completed_steps)} step(s) "
            f"already complete: {', '.join(result.completed_steps)}"
        )
    else:
        typer.echo(
            f"resuming {issue_id} — no prior verdicts on iter beads; running from top"
        )
    if result.reopened:
        typer.echo(f"reopened bead {issue_id}")
    typer.echo(result.flow_result)


@app.command()
def reconcile(
    stale_secs: int = typer.Option(
        600,
        "--stale-secs",
        min=60,
        help="Resume controllers with no process or artifact activity for this long.",
    ),
) -> None:
    """Repair abandoned flow controllers from their durable checkpoints."""
    from prefect_orchestration.reconcile import reconcile_once

    result = reconcile_once(stale_secs=stale_secs)
    typer.echo(
        f"inspected={result.inspected} resumed={len(result.resumed)} "
        f"skipped={len(result.skipped)}"
    )
    if result.resumed:
        typer.echo("resumed: " + ", ".join(result.resumed))
    if result.skipped:
        typer.echo("skipped: " + ", ".join(result.skipped), err=True)


@app.command()
def cancel(
    issue_id: str = typer.Argument(..., help="Issue whose runs should stop."),
) -> None:
    """Explicitly cancel Prefect runs and terminate this issue's role sessions."""
    from prefect_orchestration.cancel import cancel_issue

    result = cancel_issue(issue_id)
    typer.echo(
        f"cancelled {result.flow_runs} flow run(s); "
        f"terminated {result.tmux_sessions} tmux session(s)"
    )


@app.command()
def watch(
    issue_id: str = typer.Argument(
        ..., help="Beads issue id whose run should be watched live."
    ),
    replay: bool = typer.Option(
        False,
        "--replay",
        help="Dump existing run_dir artifacts + last N flow state transitions "
        "before following live.",
    ),
    replay_n: int = typer.Option(
        10,
        "--replay-n",
        help="Number of prior flow state transitions to include in --replay.",
    ),
    output_json: bool = typer.Option(
        False,
        "--json",
        help="Emit NDJSON (one JSON object per event). REPLAY_SEPARATOR is emitted "
        "as a JSON object with kind=replay-separator.",
    ),
) -> None:
    """Merge Prefect flow-state transitions + new run_dir artifacts into one feed.

    Resolves the run_dir via bd metadata (`po.rig_path` / `po.run_dir`),
    finds the most recent flow run tagged `issue_id:<id>`, and streams
    both sources with `[prefect]` / `[run-dir]` prefixes. Ctrl-C exits
    cleanly; if either source is unavailable (run finished, bd metadata
    missing, Prefect unreachable) the other still streams.
    """
    import asyncio

    try:
        loc = _run_lookup.resolve_run_dir(issue_id)
    except _run_lookup.RunDirNotFound as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    async def _find_flow_run(client: Any) -> Any | None:
        runs = await _status.find_runs_by_issue_id(client, issue_id=issue_id, limit=10)
        return runs[0] if runs else None

    def _write(line: str) -> None:
        typer.echo(line)

    def _warn(line: str) -> None:
        typer.echo(line, err=True)

    use_color = _watch.should_use_color() and not output_json

    async def _main() -> None:
        from prefect.client.orchestration import get_client

        async with get_client() as client:

            async def _factory() -> Any:
                return client

            await _watch.run_watch(
                issue_id=issue_id,
                run_dir=loc.run_dir,
                client_factory=_factory,
                find_flow_run=_find_flow_run,
                write=_write,
                warn=_warn,
                replay=replay,
                replay_n=replay_n,
                use_color=use_color,
                use_json=output_json,
            )

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        # AC2: clean exit; no traceback to the user.
        raise typer.Exit(0)


@app.command()
def spend(
    issue_id: str | None = typer.Option(
        None,
        "--issue-id",
        help="Limit to one issue's run_dir (resolved via bd metadata).",
    ),
    since: str | None = typer.Option(
        None,
        "--since",
        help="Relative (1h, 30m, 7d) or ISO-8601. Filters run_dirs by mtime.",
    ),
    by: str = typer.Option(
        "role",
        "--by",
        help="Group aggregated output by: formula | role | day (default: role).",
    ),
    rig_path: Path = typer.Option(
        Path("."),
        "--rig-path",
        help="Root of the rig (contains .planning/). Defaults to cwd.",
    ),
    output_json: bool = typer.Option(
        False, "--json", help="Output raw records as JSON array."
    ),
) -> None:
    """Estimate USD spend for PO runs by reading JSONL token traces.

    Walks .planning/ under --rig-path (or a single issue's run_dir with
    --issue-id), reads per-role JSONL files, and computes estimated cost
    from a hardcoded pricing table. Best-effort estimation — not billing-grade.

    Skips roles whose JSONL file is missing (e.g. StubBackend or cleaned up).
    """
    import json as _json

    since_dt = None
    if since is not None:
        try:
            since_dt = _status.parse_since(since)
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(2) from exc

    rig_path_abs = rig_path.resolve()

    if issue_id is not None:
        try:
            loc = _run_lookup.resolve_run_dir(issue_id)
        except _run_lookup.RunDirNotFound as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc
        formula = loc.run_dir.parent.name
        run_dirs = [(formula, issue_id, loc.run_dir)]
        rig_path_abs = loc.rig_path
    else:
        run_dirs = _spend.discover_run_dirs(rig_path_abs, since=since_dt)

    records = _spend.build_records(run_dirs, rig_path=rig_path_abs)

    if output_json:
        typer.echo(_json.dumps(_spend.to_json(records)))
        return

    rows = _spend.aggregate(records, by=by)
    typer.echo(_spend.render_table(rows, by=by))


packs_app = typer.Typer(
    no_args_is_help=True,
    help="Manage formula packs (install / uninstall / update / list).",
)


@packs_app.command("install")
def packs_install(
    spec: str = typer.Argument(
        ...,
        help="Pack to install: PyPI name (e.g. po-formulas-software-dev), "
        "git URL (git+https://..., git@..., https://.../x.git), or local path.",
    ),
    editable: bool = typer.Option(
        False,
        "--editable",
        "-e",
        help="Treat `spec` as a local path and install editable (dev workflow).",
    ),
    rig_path: Path | None = typer.Option(
        None,
        "--rig-path",
        help="Rig root to materialize the pack into (overlay CLAUDE-*.md + any "
        "external skills the pack declares). Defaults to cwd.",
        exists=False,
    ),
) -> None:
    """Install a pack into po's tool env (delegates to `uv tool`).

    PO owns pack lifecycle end-to-end (engdocs/principles.md §3). Users
    don't need to learn `uv tool install --force --with-editable …`
    incantations.

    Beyond installing the distribution, this materializes the pack into
    the rig (`--rig-path`): it copies each pack's `overlay/CLAUDE-*.md`
    into `<rig>/.claude/packs/`, and installs any external skills the pack
    declares via `[tool.po] external_skills = [...]` using the Vercel
    `skills` CLI (`npx skills add <ref> --project`) — a no-op when none
    are declared or `npx` is absent.
    """
    try:
        _packs.install(spec, editable=editable)
    except _packs.PackError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"installed {spec}")
    from prefect_orchestration.pack_overlay import apply_external_skills
    from prefect_orchestration.pack_overlay import apply_pack_index
    from prefect_orchestration.pack_overlay import (
        discover_packs as _discover_overlay_packs,
    )

    effective_rig = rig_path or Path.cwd()
    for pack in _discover_overlay_packs():
        written = apply_pack_index(pack, effective_rig)
        for f in written:
            typer.echo(f"  overlay -> {f.relative_to(effective_rig)}")
        # Materialize any external skills the pack declares (Vercel `skills`
        # CLI). Opt-in per pack via `[tool.po] external_skills`; a no-op when
        # none are declared or `npx` is absent.
        for ref in apply_external_skills(pack, effective_rig):
            typer.echo(f"  external-skill -> {ref}")


@packs_app.command("update")
def packs_update(
    name: str | None = typer.Argument(
        None,
        help="Pack name to refresh. Omit to refresh every installed pack.",
    ),
) -> None:
    """Re-install packs so entry-point metadata is rewritten.

    Entry-point groups are written at install time, not on code reload.
    This subcommand replaces the manual `uv tool install --force …`
    re-run ritual.
    """
    try:
        refreshed = _packs.update(name)
    except _packs.PackError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    if not refreshed:
        typer.echo("no packs installed; reinstalled core only.")
    else:
        typer.echo(f"refreshed: {', '.join(refreshed)}")


@packs_app.command("uninstall")
def packs_uninstall(
    name: str = typer.Argument(..., help="Pack distribution name to remove."),
) -> None:
    """Remove a pack from po's tool env."""
    try:
        _packs.uninstall(name)
    except _packs.PackError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"uninstalled {name}")


@packs_app.command("restore")
def packs_restore() -> None:
    """Rebuild core and all desired packs from PO's durable manifest."""
    try:
        restored = _packs.restore()
    except _packs.PackError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    if restored:
        typer.echo(f"restored: {', '.join(restored)}")
    else:
        typer.echo("restored core; no formula packs are recorded.")


@packs_app.command("list")
def packs_list() -> None:
    """List installed packs and what each contributes (formulas, deployments, ...)."""
    found = _packs.discover_packs()
    typer.echo(_packs.render_packs_table(found))


app.add_typer(packs_app, name="packs")

app.add_typer(
    _account.account_app,
    name="account",
    help="Manage coding-agent accounts and directory policy.",
)


app.add_typer(
    _serve.app,
    name="serve",
    help="Install/manage the Postgres + Prefect server background stack.",
)

app.add_typer(
    _env.env_app,
    name="env",
    help="Manage remote cloud envs (provision, list, teardown, attach).",
)

app.add_typer(
    cron_app,
    name="cron",
    help="Apply / list recurring cron deployments from a directory of *.toml orders.",
)


tui_app = typer.Typer(
    no_args_is_help=False,
    help="Open the Ink-based TUI dashboard, or rebuild it from source.",
)
app.add_typer(tui_app, name="tui")


@tui_app.callback(
    invoke_without_command=True,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def _tui_default(
    ctx: typer.Context,
    rig_path: Path | None = typer.Option(None, "--rig-path"),
    prefect_url: str | None = typer.Option(None, "--prefect-url"),
    refresh_ms: int | None = typer.Option(None, "--refresh-ms", min=1000),
    ascii_mode: bool = typer.Option(False, "--ascii"),
    plain: bool = typer.Option(False, "--plain"),
) -> None:
    """Default action: open the TUI. Subcommands (`update`) skip this body."""
    if ctx.invoked_subcommand is not None:
        return
    here = Path(__file__).resolve().parent.parent
    candidates = [
        here / "bin" / "po-tui",
        here / "tui" / "dist" / "po-tui",
    ]
    binary: str | None = None
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            binary = str(c)
            break
    if binary is None:
        on_path = shutil.which("po-tui")
        if on_path:
            binary = on_path
    if binary is None:
        typer.echo("po-tui binary not found.\n", err=True)
        typer.echo("Build it first:", err=True)
        typer.echo("  po tui update    # or:", err=True)
        typer.echo(f"  cd {here / 'tui'} && bun install && bun run build", err=True)
        typer.echo(
            f"  mkdir -p {here / 'bin'} && cp dist/po-tui {here / 'bin' / 'po-tui'}",
            err=True,
        )
        raise SystemExit(1)
    extra = list(ctx.args or [])
    if rig_path is not None:
        extra.extend(["--rig-path", str(rig_path)])
    if prefect_url is not None:
        extra.extend(["--prefect-url", prefect_url])
    if refresh_ms is not None:
        extra.extend(["--refresh-ms", str(refresh_ms)])
    if ascii_mode:
        extra.append("--ascii")
    if plain:
        extra.append("--plain")
    os.execvp(binary, [binary, *extra])


@tui_app.command("update")
def _tui_update(
    skip_install: bool = typer.Option(
        False,
        "--skip-install",
        help="Skip `bun install` (faster on no-dep-change rebuilds).",
    ),
    no_copy: bool = typer.Option(
        False,
        "--no-copy",
        help="Skip the `cp dist/po-tui bin/po-tui` step.",
    ),
) -> None:
    """Rebuild the TUI binary from `tui/` source (`bun install` + `bun build`).

    Writes the binary to `<repo>/tui/dist/po-tui` and copies it to
    `<repo>/bin/po-tui` (the path `po tui` prefers). Use after
    `git pull` brings in TUI source changes, or after editing `tui/src/*`.

    Requires `bun` on PATH (see https://bun.sh).
    """
    if shutil.which("bun") is None:
        typer.echo(
            "bun not found on PATH. Install: curl -fsSL https://bun.sh/install | bash",
            err=True,
        )
        raise SystemExit(1)
    here = Path(__file__).resolve().parent.parent
    tui_dir = here / "tui"
    if not tui_dir.is_dir():
        typer.echo(f"tui source not found at {tui_dir}", err=True)
        raise SystemExit(1)

    if not skip_install:
        typer.echo(f"→ bun install (in {tui_dir})")
        rc = subprocess.run(["bun", "install"], cwd=tui_dir).returncode
        if rc != 0:
            typer.echo("bun install failed", err=True)
            raise SystemExit(rc)

    typer.echo(f"→ bun run build (in {tui_dir})")
    rc = subprocess.run(["bun", "run", "build"], cwd=tui_dir).returncode
    if rc != 0:
        typer.echo("bun run build failed", err=True)
        raise SystemExit(rc)

    dist_bin = tui_dir / "dist" / "po-tui"
    if not dist_bin.is_file():
        typer.echo(f"build did not produce {dist_bin}", err=True)
        raise SystemExit(1)
    typer.echo(f"  built: {dist_bin}  ({dist_bin.stat().st_size // (1024 * 1024)} MB)")

    if not no_copy:
        bin_dir = here / "bin"
        bin_dir.mkdir(exist_ok=True)
        target = bin_dir / "po-tui"
        # Atomic replace via temp file so a running TUI process holding
        # the old inode doesn't break the swap with `Text file busy`.
        tmp = target.with_suffix(".new")
        shutil.copy2(dist_bin, tmp)
        os.chmod(tmp, 0o755)
        os.replace(tmp, target)
        typer.echo(f"  copied to: {target}")

    typer.echo("✓ tui updated. run `po tui` to launch.")


@app.command()
def attach(
    issue_id: str = typer.Argument(
        ..., help="Beads issue id whose tmux agent session(s) you want to attach to."
    ),
    role: str | None = typer.Option(
        None,
        "--role",
        help="Pick a specific role's session. Required when multiple roles "
        "exist and stdin isn't a TTY.",
    ),
    list_only: bool = typer.Option(
        False,
        "--list",
        help="List the resolved attach target(s) and exit; do not attach.",
    ),
    print_argv: bool = typer.Option(
        False,
        "--print-argv",
        help="Print the argv that would be exec'd and exit (debug / e2e harness).",
    ),
) -> None:
    """Attach to the tmux agent session for a beads issue's run.

    Wraps `kubectl exec -it <pod> -- tmux attach` when the bead carries
    `po.k8s_pod` metadata; falls through to `tmux attach` directly on
    the host otherwise.
    """
    try:
        loc = _run_lookup.resolve_run_dir(issue_id)
    except _run_lookup.RunDirNotFound as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    roles = _attach.discover_roles(loc.run_dir)
    if not roles:
        typer.echo(
            f"no roles found in {loc.run_dir}/metadata.json — "
            "the flow may not have started any agent sessions yet.",
            err=True,
        )
        raise typer.Exit(3)

    bead_meta = _attach.fetch_bead_metadata(issue_id)

    if list_only:
        for r in roles:
            target = _attach.resolve_attach_target(
                issue=issue_id, role=r, bead_metadata=bead_meta
            )
            if isinstance(target, _attach.K8sTarget):
                ctx = target.context or "<current-context>"
                typer.echo(
                    f"{r}\tk8s\tcontext={ctx} ns={target.namespace} "
                    f"pod={target.pod} session={target.session}"
                )
            else:
                typer.echo(f"{r}\tlocal\tsession={target.session}")
        return

    chosen: str
    if role is not None:
        if role not in roles:
            typer.echo(
                f"unknown role {role!r}. Known roles: {', '.join(roles)}",
                err=True,
            )
            raise typer.Exit(4)
        chosen = role
    elif len(roles) == 1:
        chosen = roles[0]
    else:
        is_tty = bool(getattr(sys.stdin, "isatty", lambda: False)())
        if not is_tty:
            typer.echo(
                f"multiple roles available ({', '.join(roles)}); "
                "specify --role <role> (stdin is not a TTY).",
                err=True,
            )
            raise typer.Exit(5)
        typer.echo("Multiple roles available:")
        for i, r in enumerate(roles, start=1):
            typer.echo(f"  [{i}] {r}")
        try:
            sel = input("pick number (or role name): ").strip()
        except EOFError:
            raise typer.Exit(5) from None
        if sel.isdigit() and 1 <= int(sel) <= len(roles):
            chosen = roles[int(sel) - 1]
        elif sel in roles:
            chosen = sel
        else:
            typer.echo(f"invalid selection {sel!r}", err=True)
            raise typer.Exit(5)

    # Env-backed runs: delegate to driver.attach_argv before k8s/local path.
    env_argv = _attach.resolve_env_attach_argv(
        issue=issue_id, role=chosen, bead_metadata=bead_meta
    )
    if env_argv:
        if print_argv:
            typer.echo(" ".join(env_argv))
            return
        os.execvp(env_argv[0], env_argv)

    target = _attach.resolve_attach_target(
        issue=issue_id, role=chosen, bead_metadata=bead_meta
    )

    if isinstance(target, _attach.K8sTarget):
        if target.context is None:
            typer.echo(
                f"warning: bead has {_attach.META_K8S_POD}={target.pod} but no "
                f"{_attach.META_K8S_CONTEXT} — using your current kubeconfig context. "
                "Set PO_KUBE_CONTEXT on the worker Deployment to make this explicit.",
                err=True,
            )
        status, detail = _attach.probe_pod(target)
        if status == "gone":
            typer.echo(
                f"pod gone, run was on {target.pod!r} ({detail}). "
                f"Try `po retry {issue_id}` to relaunch.",
                err=True,
            )
            raise typer.Exit(6)
        if status == "forbidden":
            typer.echo(
                f"RBAC: caller needs pods/exec in namespace {target.namespace!r} "
                f"({detail}).",
                err=True,
            )
            raise typer.Exit(7)
        if status == "unknown":
            typer.echo(f"kubectl probe failed: {detail}", err=True)
            raise typer.Exit(8)
        argv = _attach.build_kubectl_argv(target)
    else:
        argv = _attach.build_local_argv(target)

    if print_argv:
        typer.echo(" ".join(argv))
        return

    os.execvp(argv[0], argv)


def main() -> None:
    """Entry point for the `po` console script.

    Dispatch order:
      1. If argv[0] is not a core Typer verb and matches a registered
         `po.commands` entry, invoke that callable with `_parse_kwargs`
         on the remaining argv. (Per principle §4: pack-shipped utility
         ops skip Prefect overhead and dispatch as `po <command>`.)
      2. Otherwise hand off to the Typer `app` for normal subcommand
         routing (including `--help`, `list`, `run`, etc.).
    """
    argv = sys.argv[1:]
    if argv and not argv[0].startswith("-"):
        first = argv[0]
        reserved = _commands.core_verbs()
        if first not in reserved:
            registry = _commands.load_commands()
            if first in registry:
                fn = registry[first]
                # Leading bare tokens are positional subcommands (e.g.
                # `po director start`); everything from the first `--` on is
                # parsed as kwargs. Commands that take only kwargs are
                # unaffected (no leading positionals to collect).
                rest = list(argv[1:])
                pos: list[str] = []
                while rest and not rest[0].startswith("-"):
                    pos.append(rest.pop(0))
                kwargs = _parse_kwargs(rest)
                try:
                    result = fn(*pos, **kwargs)
                except TypeError as exc:
                    typer.echo(f"bad arguments for {first}: {exc}", err=True)
                    typer.echo(f"run `po show {first}` to see the signature", err=True)
                    raise SystemExit(2) from exc
                if result is not None:
                    typer.echo(result)
                return
    app()


if __name__ == "__main__":
    main()
