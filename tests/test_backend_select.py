"""Unit tests for `prefect_orchestration.backend_select`."""

from __future__ import annotations

import pytest

from prefect_orchestration.agent_session import (
    ClaudeCliBackend,
    StubBackend,
    TmuxClaudeBackend,
)
from prefect_orchestration.backend_select import select_default_backend


def test_explicit_stub(monkeypatch):
    monkeypatch.setenv("PO_BACKEND", "stub")
    assert select_default_backend() is StubBackend


def test_explicit_cli_overrides_tmux(monkeypatch):
    monkeypatch.setenv("PO_BACKEND", "cli")
    # Even with tmux available + a TTY, cli wins.
    assert (
        select_default_backend(have_tmux=True, is_tty=True)
        is ClaudeCliBackend
    )


def test_explicit_tmux_with_tmux_present(monkeypatch):
    monkeypatch.setenv("PO_BACKEND", "tmux")
    assert (
        select_default_backend(have_tmux=True, is_tty=True)
        is TmuxClaudeBackend
    )


def test_explicit_tmux_without_tmux_raises(monkeypatch):
    monkeypatch.setenv("PO_BACKEND", "tmux")
    with pytest.raises(RuntimeError, match="tmux"):
        select_default_backend(have_tmux=False, is_tty=True)


def test_auto_tmux_plus_tty_picks_tmux(monkeypatch):
    monkeypatch.delenv("PO_BACKEND", raising=False)
    assert (
        select_default_backend(have_tmux=True, is_tty=True)
        is TmuxClaudeBackend
    )


def test_auto_tmux_but_no_tty_picks_cli(monkeypatch):
    monkeypatch.delenv("PO_BACKEND", raising=False)
    # The container case: tmux installed but no TTY attached.
    assert (
        select_default_backend(have_tmux=True, is_tty=False)
        is ClaudeCliBackend
    )


def test_auto_no_tmux_picks_cli(monkeypatch):
    monkeypatch.delenv("PO_BACKEND", raising=False)
    assert (
        select_default_backend(have_tmux=False, is_tty=True)
        is ClaudeCliBackend
    )


def test_override_param_beats_env(monkeypatch):
    monkeypatch.setenv("PO_BACKEND", "tmux")
    assert (
        select_default_backend(override="cli", have_tmux=True, is_tty=True)
        is ClaudeCliBackend
    )


def test_empty_override_ignores_env(monkeypatch):
    """`override=''` means 'auto, ignoring env'."""
    monkeypatch.setenv("PO_BACKEND", "stub")
    assert (
        select_default_backend(override="", have_tmux=False, is_tty=False)
        is ClaudeCliBackend
    )
