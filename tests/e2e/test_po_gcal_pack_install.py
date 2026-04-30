"""E2E: po-gcal pack registers commands + doctor checks once installed.

Skipped when the sibling pack isn't importable, so this rig's CI stays
green without po-gcal present. Doesn't shell out `po packs install` (would
mutate the user's `uv tool` state) — instead asserts that
`importlib.metadata` already sees the entry points after a manual
`po packs install --editable`.
"""

from __future__ import annotations

import importlib.metadata as im

import pytest

pytest.importorskip("po_gcal")


def _ep_names(group: str) -> set[str]:
    return {ep.name for ep in im.entry_points(group=group)}


def test_three_commands_registered_via_entry_points() -> None:
    names = _ep_names("po.commands")
    assert {"gcal-today", "gcal-create", "gcal-free"} <= names


def test_two_doctor_checks_registered_via_entry_points() -> None:
    targets = {ep.value for ep in im.entry_points(group="po.doctor_checks")}
    assert "po_gcal.checks:creds_present" in targets
    assert "po_gcal.checks:calendar_reachable" in targets


def test_commands_callables_resolve() -> None:
    eps = {ep.name: ep for ep in im.entry_points(group="po.commands")}
    for name in ("gcal-today", "gcal-create", "gcal-free"):
        fn = eps[name].load()
        assert callable(fn), f"{name} entry point did not resolve to a callable"
