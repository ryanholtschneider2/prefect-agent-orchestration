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
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

import typer

from prefect_orchestration import artifacts as _artifacts
from prefect_orchestration import deployments as _deployments
from prefect_orchestration import doctor as _doctor
from prefect_orchestration import packs as _packs
from prefect_orchestration import retry as _retry
from prefect_orchestration import run_lookup as _run_lookup
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
    """List formulas registered via the `po.formulas` entry-point group."""
    formulas = _load_formulas()
    if not formulas:
        typer.echo("no formulas installed.")
        typer.echo("install a pack with `uv add <pack>` or `pip install <pack>`")
        typer.echo('packs declare formulas via `[project.entry-points."po.formulas"]`.')
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
    name: str = typer.Argument(..., help="Formula name from `po list`"),
) -> None:
    """Run a registered formula. Pass flow kwargs after the name:

    po run software-dev-full --issue-id sr-8yu.3 --rig site --rig-path ./site
    """
    _autoconfigure_prefect_api()
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

    Checks: `bd` CLI, Prefect server reachability, at least one work
    pool, formula + deployment entry points load, uv-tool install
    freshness, and LOGFIRE telemetry token. Exits 1 if any critical
    check fails; warnings never affect the exit code.
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
) -> None:
    """List active / recent flow runs grouped by beads `issue_id` tag.

    `prefect flow-run ls` is unaware of bead IDs. This pulls recent runs
    from the Prefect server, groups by the `issue_id:<id>` tag PO stamps
    onto each run, and prints one row per issue. Always exits 0 — an
    observation command, not a check.
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
        typer.echo(_status.render_table(groups))

    anyio.run(_main)


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

    try:
        metadata = _sessions.load_metadata(loc.run_dir)
    except _sessions.MetadataNotFound as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(3) from exc

    if resume is not None:
        uuid = _sessions.lookup_session(metadata, resume)
        if uuid is None:
            typer.echo(f"no session recorded for role {resume!r}", err=True)
            raise typer.Exit(4)
        typer.echo(_sessions.resume_command(uuid))
        return

    rows = _sessions.build_rows(loc.run_dir, metadata)
    typer.echo(_sessions.render_table(rows))


@app.command()
def retry(
    issue_id: str = typer.Argument(
        ..., help="Beads issue id whose run_dir should be archived + relaunched."
    ),
    keep_sessions: bool = typer.Option(
        False,
        "--keep-sessions",
        help="Preserve per-role Claude session UUIDs from the prior run's metadata.json.",
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


@app.command()
def install(
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


@app.command()
def update(
    name: str | None = typer.Argument(
        None,
        help="Pack name to refresh. Omit to refresh every installed pack.",
    ),
) -> None:
    """Re-install packs so entry-point metadata is rewritten.

    Entry-point groups are written at install time, not on code reload.
    This verb replaces the manual `uv tool install --force …` re-run
    ritual.
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


@app.command()
def uninstall(
    name: str = typer.Argument(..., help="Pack distribution name to remove."),
) -> None:
    """Remove a pack from po's tool env."""
    try:
        _packs.uninstall(name)
    except _packs.PackError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"uninstalled {name}")


@app.command()
def packs() -> None:
    """List installed packs and what each contributes (formulas, deployments, ...)."""
    found = _packs.discover_packs()
    typer.echo(_packs.render_packs_table(found))


if __name__ == "__main__":
    app()
