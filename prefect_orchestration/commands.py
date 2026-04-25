"""`po.commands` entry-point group — pack-shipped utility callables.

Packs ship small non-orchestrated utility ops (check budget, tail logs,
summarize verdicts, reconcile CRM, etc.) that aren't formulas and
shouldn't pay the Prefect overhead `po run` adds. These are dispatched
via `po <command>` (NOT `po run <command>`) and discovered through the
`po.commands` entry-point group:

    [project.entry-points."po.commands"]
    summarize-verdicts = "po_formulas.commands:summarize_verdicts"

Core has no knowledge of specific commands — they're pluggable, exactly
like formulas.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any, Callable


def load_commands() -> dict[str, Callable[..., Any]]:
    """Return {name: loaded_callable} for every `po.commands` entry point.

    Failed loads are skipped silently — the CLI surfaces the warning
    via the same path it uses for formulas (best-effort discovery; one
    bad pack must not break `po list`).
    """
    out: dict[str, Callable[..., Any]] = {}
    try:
        eps = entry_points(group="po.commands")
    except TypeError:
        eps = entry_points().get("po.commands", [])  # type: ignore[assignment]
    for ep in eps:
        try:
            out[ep.name] = ep.load()
        except Exception:
            # Caller (cli) will re-emit a warning if/when it lists.
            continue
    return out


def core_verbs() -> set[str]:
    """Return the set of verb names registered as Typer subcommands.

    Reads off the live `cli.app` Typer object so adding a new
    `@app.command()` automatically extends the reserved-verb set.
    Imports `cli` lazily to avoid circular import at module load.
    """
    from prefect_orchestration import cli  # local import to break cycle

    names: set[str] = set()
    for cmd in getattr(cli.app, "registered_commands", []):
        # Typer stores the explicit name (if given) on the CommandInfo,
        # else falls back to the callback function name (with `_` → `-`).
        explicit = getattr(cmd, "name", None)
        if explicit:
            names.add(explicit)
            continue
        cb = getattr(cmd, "callback", None)
        fn_name = getattr(cb, "__name__", None)
        if fn_name:
            # Typer auto-derives subcommand name from snake_case → kebab-case.
            names.add(fn_name.replace("_", "-").rstrip("-"))
    return names


def find_command_collisions(
    commands_by_pack: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Return {pack_name: [colliding_names]} for any cmd that shadows a core verb.

    Pure helper used at install/update time — the caller decides what
    error to raise.
    """
    reserved = core_verbs()
    out: dict[str, list[str]] = {}
    for pack, names in commands_by_pack.items():
        offenders = sorted(n for n in names if n in reserved)
        if offenders:
            out[pack] = offenders
    return out
