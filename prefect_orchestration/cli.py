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
import os
import shutil
import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

import typer

from prefect_orchestration import artifacts as _artifacts
from prefect_orchestration import attach as _attach
from prefect_orchestration import commands as _commands
from prefect_orchestration import deployments as _deployments
from prefect_orchestration import doctor as _doctor
from prefect_orchestration import packs as _packs
from prefect_orchestration import resume as _resume
from prefect_orchestration import retry as _retry
from prefect_orchestration import run_lookup as _run_lookup
from prefect_orchestration import scheduling as _scheduling
from prefect_orchestration import scratch_loader as _scratch_loader
from prefect_orchestration import serve as _serve
from prefect_orchestration import sessions as _sessions
from prefect_orchestration import status as _status
from prefect_orchestration import watch as _watch

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
    """List formulas + commands registered via `po.formulas` / `po.commands`."""
    formulas = _load_formulas()
    cmds = _commands.load_commands()
    if not formulas and not cmds:
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
def show(name: str) -> None:
    """Show the signature + docstring of a registered formula or command."""
    formulas = _load_formulas()
    if name in formulas:
        flow_obj = formulas[name]
        fn = getattr(flow_obj, "fn", flow_obj)
        typer.echo(f"{name} (formula) — {flow_obj.__module__}:{flow_obj.__name__}")
        sig = inspect.signature(fn)
        typer.echo(f"\nSignature:\n  {fn.__name__}{sig}")
        doc = inspect.getdoc(fn)
        if doc:
            typer.echo(f"\nDoc:\n{doc}")
        return

    cmds = _commands.load_commands()
    if name in cmds:
        fn = cmds[name]
        module = getattr(fn, "__module__", "?")
        fn_name = getattr(fn, "__name__", str(fn))
        typer.echo(f"{name} (command) — {module}:{fn_name}")
        try:
            sig = inspect.signature(fn)
            typer.echo(f"\nSignature:\n  {fn_name}{sig}")
        except (TypeError, ValueError):
            typer.echo("\nSignature: <unavailable>")
        doc = inspect.getdoc(fn)
        if doc:
            typer.echo(f"\nDoc:\n{doc}")
        return

    typer.echo(f"no formula or command named {name!r}. Run `po list`.", err=True)
    raise typer.Exit(1)


_DEFAULT_PREFECT_API = "http://127.0.0.1:4200/api"


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
        "--time",
        help="Schedule the formula's <name>-manual deployment for a future "
        "time instead of running synchronously. Relative duration "
        "(2h, 30m, 1d, +30m) or ISO-8601 with timezone "
        "(2026-04-25T09:00:00-04:00 / 2026-04-25T13:00:00Z). Requires "
        "`po deploy --apply` for <formula>-manual first; CLI prints a "
        "worker-startup reminder.",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Claude model alias or full id (sonnet|opus|haiku|<full id>). "
        "Stamps PO_MODEL_CLI; per-role agents/<role>/config.toml beats this.",
    ),
    effort: str | None = typer.Option(
        None,
        "--effort",
        help="Claude effort level (low|medium|high|xhigh|max). "
        "Stamps PO_EFFORT_CLI; per-role config.toml beats this.",
    ),
    start_command: str | None = typer.Option(
        None,
        "--start-command",
        help="Override the claude CLI invocation prefix (default: "
        "'claude --dangerously-skip-permissions'). Stamps PO_START_COMMAND_CLI.",
    ),
) -> None:
    """Run a registered formula or an ad-hoc scratch flow.

    Registered:  po run software-dev-full --issue-id sr-8yu.3 --rig site --rig-path ./site
    Scheduled:   po run software-dev-full --time 2h --issue-id ...
    Scratch:     po run --from-file ./my_flow.py [--name foo] --arg value
    """
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

    if when is not None:
        _run_scheduled(
            name=name, from_file=from_file, when=when, extras=extras,
            model=model, effort=effort, start_command=start_command,
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
        if name is None:
            typer.echo(
                "missing formula name. Run `po list`, or use --from-file <path>.",
                err=True,
            )
            raise typer.Exit(2)
        formulas = _load_formulas()
        if name not in formulas:
            typer.echo(f"no formula named {name!r}. Run `po list`.", err=True)
            raise typer.Exit(1)
        flow_obj = formulas[name]
        label = name

    kwargs = _parse_kwargs(extras)
    _apply_runtime_overrides(
        flow_obj, kwargs,
        model=model, effort=effort, start_command=start_command,
    )

    # SIGINT / SIGTERM cleanup: when the user Ctrl-Cs `po run` (or the
    # process is killed), the spawned tmux sessions are detached so they
    # outlive us. Drain the in-process registry before exiting so we
    # don't leak zombie claude+tmux pairs eating rate-limit slots
    # indefinitely (sav.3).
    import signal

    from prefect_orchestration import tmux_tracker

    def _signal_cleanup(signum: int, _frame: Any) -> None:
        n = tmux_tracker.kill_all()
        if n:
            typer.echo(
                f"[po] killed {n} tmux session(s)/window(s) on signal {signum}",
                err=True,
            )
        # 128 + signum — conventional exit code for signal-terminated.
        raise typer.Exit(128 + signum)

    prior_int = signal.signal(signal.SIGINT, _signal_cleanup)
    prior_term = signal.signal(signal.SIGTERM, _signal_cleanup)
    try:
        result = flow_obj(**kwargs)
    except TypeError as exc:
        typer.echo(f"bad arguments for {label}: {exc}", err=True)
        if from_file is None:
            typer.echo(f"run `po show {label}` to see the signature", err=True)
        raise typer.Exit(2) from exc
    finally:
        signal.signal(signal.SIGINT, prior_int)
        signal.signal(signal.SIGTERM, prior_term)
    typer.echo(result)


_RUNTIME_KNOBS: tuple[tuple[str, str], ...] = (
    ("model", "PO_MODEL_CLI"),
    ("effort", "PO_EFFORT_CLI"),
    ("start_command", "PO_START_COMMAND_CLI"),
)


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


def _run_scheduled(
    *,
    name: str | None,
    from_file: Path | None,
    when: str,
    extras: list[str],
    model: str | None = None,
    effort: str | None = None,
    start_command: str | None = None,
) -> None:
    """Implementation of `po run <formula> --time <when>`.

    Resolves `<formula>-manual` on the Prefect server (per
    engdocs/principles.md §1 — collapses
    `prefect deployment run <flow>/<formula>-manual --start-in 2h`
    into the by-convention shape) and submits a one-shot scheduled
    flow-run with the parsed CLI kwargs as parameters.
    """
    if from_file is not None:
        typer.echo(
            "--time and --from-file are mutually exclusive: scratch flows "
            "aren't registered as deployments and have no manual deployment "
            "to schedule against.",
            err=True,
        )
        raise typer.Exit(2)
    if name is None:
        typer.echo(
            "--time requires a formula name (no scratch-flow scheduling).",
            err=True,
        )
        raise typer.Exit(2)
    formulas = _load_formulas()
    if name not in formulas:
        typer.echo(f"no formula named {name!r}. Run `po list`.", err=True)
        raise typer.Exit(1)

    try:
        scheduled_time = _scheduling.parse_when(when)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc

    kwargs = _parse_kwargs(extras)
    _apply_runtime_overrides(
        formulas[name], kwargs,
        model=model, effort=effort, start_command=start_command,
    )
    issue_id = kwargs.get("issue_id")

    import asyncio

    from prefect.client.orchestration import get_client

    async def _go() -> tuple[Any, str]:
        async with get_client() as client:
            return await _scheduling.submit_scheduled_run(
                client=client,
                formula=name,
                parameters=kwargs,
                scheduled_time=scheduled_time,
                issue_id=issue_id if isinstance(issue_id, str) else None,
            )

    try:
        flow_run, full_name = asyncio.run(_go())
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
        f"scheduled flow-run {flow_run.id} ({full_name}) at {scheduled_time.isoformat()}"
    )
    typer.echo(
        f"queued for {when}; ensure `prefect worker start --pool po` "
        f"is running before then."
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
        False, "--verdicts", help="Print only the verdicts/*.json files."
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
    verdicts/*.json. Missing files render as `(missing)` — the command never
    aborts on a partial run.
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


@app.command()
def doctor() -> None:
    """Read-only health check of the full PO wiring.

    Runs core checks (`bd` CLI, Prefect server reachability, at least one
    work pool, formula + deployment entry points load, uv-tool install
    freshness, LOGFIRE telemetry token), then any checks contributed by
    installed packs via the `po.doctor_checks` entry-point group. Each
    pack check is wrapped in a 5s soft timeout (yellow on timeout). Exits
    1 if any check is red; warnings never affect the exit code.
    """
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
        200, "--limit", help="Max flow runs to fetch from server."
    ),
    include_zombies: bool = typer.Option(
        False, "--include-zombies",
        help="Show 'Running' flows whose rig_path no longer exists on disk "
        "(usually pytest fixtures whose process died before reaching a "
        "terminal state). Hidden by default.",
    ),
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
        except Exception as exc:  # noqa: BLE001 — AC3: observation, no tracebacks
            api_url = os.environ.get("PREFECT_API_URL", "<unset>")
            typer.echo(
                f"error: could not query Prefect server at {api_url}: {exc}",
                err=True,
            )
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
        False, "--any",
        help="Exit when ANY of the given issues closes (default: wait for ALL).",
    ),
    timeout: int = typer.Option(
        3600, "--timeout", "-t",
        help="Maximum seconds to wait before giving up (default: 3600 = 1h).",
    ),
    poll: int = typer.Option(
        30, "--poll", "-p",
        help="Seconds between bd-show polls (default: 30).",
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q",
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
        "failed:", "cap-exhausted", "nudge failed", "force-closed",
        "regression:", "rejected:",
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
                f"timeout after {timeout}s; still open: {still_open}", err=True,
            )
            raise typer.Exit(2)

        if not quiet:
            still_open = [i for i in issue_ids if i not in seen_closed]
            typer.echo(f"  waiting on {len(still_open)} of {len(issue_ids)}: {still_open}")
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
    formula: str = typer.Option(
        _retry.DEFAULT_FORMULA,
        "--formula",
        help="Formula entry-point name to relaunch.",
    ),
) -> None:
    """Archive an issue's run_dir and re-run its formula from scratch.

    Looks up `(rig_path, run_dir)` from bd metadata, archives the
    run_dir to a `.bak-<utc-timestamp>` sibling, reopens the bead if
    closed, and invokes the formula in-process. Refuses to proceed if
    another flow for this issue is still Running on the Prefect server
    (pass `--force` to bypass).
    """
    try:
        result = _retry.retry_issue(
            issue_id,
            keep_sessions=keep_sessions,
            rig=rig,
            force=force,
            formula=formula,
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
) -> None:
    """Resume a failed flow without archiving its run_dir.

    Unlike `po retry` (which archives run_dir → `.bak-<utc>` and starts
    fresh from triage), `po resume` preserves the run_dir as-is. Steps
    whose `verdicts/<step>.json` already exists are skipped — the
    formula's `prompt_for_verdict` short-circuits via `PO_RESUME=1` and
    returns the existing verdict instead of re-prompting the agent.

    Use this when a wave wedges deep in the DAG (e.g. on review with
    triage/baseline/plan/build/lint/test verdicts already written): it
    picks up at the failing step instead of burning 10+ min re-running
    the upstream successes.
    """
    try:
        result = _resume.resume_issue(
            issue_id,
            rig=rig,
            force=force,
            formula=formula,
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
        typer.echo(f"resuming {issue_id} — no prior verdicts on disk; running from top")
    if result.reopened:
        typer.echo(f"reopened bead {issue_id}")
    typer.echo(result.flow_result)


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

    use_color = _watch.should_use_color()

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
            )

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        # AC2: clean exit; no traceback to the user.
        raise typer.Exit(0)


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
) -> None:
    """Install a pack into po's tool env (delegates to `uv tool`).

    PO owns pack lifecycle end-to-end (engdocs/principles.md §3). Users
    don't need to learn `uv tool install --force --with-editable …`
    incantations.
    """
    try:
        _packs.install(spec, editable=editable)
    except _packs.PackError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"installed {spec}")


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


@packs_app.command("list")
def packs_list() -> None:
    """List installed packs and what each contributes (formulas, deployments, ...)."""
    found = _packs.discover_packs()
    typer.echo(_packs.render_packs_table(found))


app.add_typer(packs_app, name="packs")


app.add_typer(
    _serve.app,
    name="serve",
    help="Install/manage the Postgres + Prefect server background stack.",
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
def _tui_default(ctx: typer.Context) -> None:
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
        typer.echo(f"  po tui update    # or:", err=True)
        typer.echo(f"  cd {here / 'tui'} && bun install && bun run build", err=True)
        typer.echo(
            f"  mkdir -p {here / 'bin'} && cp dist/po-tui {here / 'bin' / 'po-tui'}",
            err=True,
        )
        raise SystemExit(1)
    extra = ctx.args or []
    os.execvp(binary, [binary, *extra])


@tui_app.command("update")
def _tui_update(
    skip_install: bool = typer.Option(
        False, "--skip-install", help="Skip `bun install` (faster on no-dep-change rebuilds)."
    ),
    no_copy: bool = typer.Option(
        False, "--no-copy", help="Skip the `cp dist/po-tui bin/po-tui` step.",
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
                kwargs = _parse_kwargs(argv[1:])
                try:
                    result = fn(**kwargs)
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
