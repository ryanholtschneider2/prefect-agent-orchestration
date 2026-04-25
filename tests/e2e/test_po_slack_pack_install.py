"""E2E: po-slack pack registers commands + doctor checks once installed.

Skipped when the sibling pack isn't importable, so this rig's CI stays
green without po-slack present. Doesn't shell out `po install` (would
mutate the user's `uv tool` state) — instead asserts that
`importlib.metadata` already sees the entry points after a manual
`po install --editable /path/to/po-slack`.
"""

from __future__ import annotations

import importlib.metadata as im

import pytest

pytest.importorskip("po_slack")


def _ep_names(group: str) -> set[str]:
    return {ep.name for ep in im.entry_points(group=group)}


def test_three_commands_registered_via_entry_points() -> None:
    names = _ep_names("po.commands")
    assert {"slack-post", "slack-upload", "slack-react"} <= names


def test_two_doctor_checks_registered_via_entry_points() -> None:
    targets = {ep.value for ep in im.entry_points(group="po.doctor_checks")}
    assert "po_slack.checks:bot_token_valid" in targets
    assert "po_slack.checks:workspace_reachable" in targets


def test_commands_callables_resolve() -> None:
    eps = {ep.name: ep for ep in im.entry_points(group="po.commands")}
    for name in ("slack-post", "slack-upload", "slack-react"):
        fn = eps[name].load()
        assert callable(fn), f"{name} entry point did not resolve to a callable"
