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
    Also walks `add_typer` sub-groups (e.g. `po packs install`) so
    nested verbs and the group name itself are reserved against
    pack-shipped command shadowing. Imports `cli` lazily to avoid
    circular import at module load.
    """
    from prefect_orchestration import cli  # local import to break cycle

    def _name_of(cmd: object) -> str | None:
        explicit = getattr(cmd, "name", None)
        if explicit:
            return str(explicit)
        cb = getattr(cmd, "callback", None)
        fn_name = getattr(cb, "__name__", None)
        if fn_name:
            return fn_name.replace("_", "-").rstrip("-")
        return None

    names: set[str] = set()
    for cmd in getattr(cli.app, "registered_commands", []):
        n = _name_of(cmd)
        if n:
            names.add(n)
    for group in getattr(cli.app, "registered_groups", []):
        group_name = getattr(group, "name", None)
        if group_name:
            names.add(str(group_name))
        sub_app = getattr(group, "typer_instance", None)
        for sub_cmd in getattr(sub_app, "registered_commands", []) if sub_app else []:
            n = _name_of(sub_cmd)
            if n:
                names.add(n)
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
