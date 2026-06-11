"""Pack-shipped `po.commands` utility ops for po-hello.

These dispatch as `po <command>` (NOT `po run`) and skip Prefect overhead.
Signature convention: plain callables, `print()` to stdout, `raise SystemExit(2)`
on error. Register each in pyproject under [project.entry-points."po.commands"].
"""

from __future__ import annotations


def ping() -> None:
    """Smoke command — prove the pack is installed and discoverable."""
    print("po-hello: pong")
