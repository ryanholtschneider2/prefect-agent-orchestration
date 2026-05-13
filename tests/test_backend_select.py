"""Unit tests for `prefect_orchestration.backend_select`."""

from __future__ import annotations

import pytest

from prefect_orchestration.agent_session import (
    ClaudeCliBackend,
    CodexCliBackend,
    StubBackend,
    TmuxClaudeBackend,
    TmuxCodexBackend,
)
from prefect_orchestration.backend_select import select_default_backend
from prefect_orchestration.backend_select import adapt_backend_to_start_command


def test_explicit_stub(monkeypatch):
    monkeypatch.setenv("PO_BACKEND", "stub")
    assert select_default_backend() is StubBackend


def test_explicit_cli_overrides_tmux(monkeypatch):
    monkeypatch.setenv("PO_BACKEND", "cli")
    # Even with tmux available + a TTY, cli wins.
    assert select_default_backend(have_tmux=True, is_tty=True) is ClaudeCliBackend


def test_explicit_tmux_with_tmux_present(monkeypatch):
    monkeypatch.setenv("PO_BACKEND", "tmux")
    assert select_default_backend(have_tmux=True, is_tty=True) is TmuxClaudeBackend


def test_explicit_codex_cli(monkeypatch):
    monkeypatch.setenv("PO_BACKEND", "codex-cli")
    assert select_default_backend() is CodexCliBackend


def test_explicit_codex_tmux(monkeypatch):
    monkeypatch.setenv("PO_BACKEND", "codex-tmux")
    assert select_default_backend(have_tmux=True, is_tty=True) is TmuxCodexBackend


def test_explicit_codex_tmux_without_tmux_raises(monkeypatch):
    monkeypatch.setenv("PO_BACKEND", "codex-tmux")
    with pytest.raises(RuntimeError, match="tmux"):
        select_default_backend(have_tmux=False, is_tty=True)


def test_explicit_tmux_without_tmux_raises(monkeypatch):
    monkeypatch.setenv("PO_BACKEND", "tmux")
    with pytest.raises(RuntimeError, match="tmux"):
        select_default_backend(have_tmux=False, is_tty=True)


def test_auto_tmux_plus_tty_picks_tmux(monkeypatch):
    monkeypatch.delenv("PO_BACKEND", raising=False)
    assert select_default_backend(have_tmux=True, is_tty=True) is TmuxClaudeBackend


def test_auto_tmux_but_no_tty_picks_cli(monkeypatch):
    monkeypatch.delenv("PO_BACKEND", raising=False)
    # The container case: tmux installed but no TTY attached.
    assert select_default_backend(have_tmux=True, is_tty=False) is ClaudeCliBackend


def test_auto_no_tmux_picks_cli(monkeypatch):
    monkeypatch.delenv("PO_BACKEND", raising=False)
    assert select_default_backend(have_tmux=False, is_tty=True) is ClaudeCliBackend


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


def test_adapt_backend_to_start_command_upgrades_tmux_claude_to_codex():
    assert (
        adapt_backend_to_start_command(
            TmuxClaudeBackend,
            "codex exec --dangerously-bypass-approvals-and-sandbox",
        )
        is TmuxCodexBackend
    )


def test_adapt_backend_to_start_command_upgrades_cli_claude_to_codex():
    assert (
        adapt_backend_to_start_command(
            ClaudeCliBackend,
            "codex exec --dangerously-bypass-approvals-and-sandbox",
        )
        is CodexCliBackend
    )


def test_adapt_backend_to_start_command_keeps_explicit_family_for_unknown_binary():
    assert (
        adapt_backend_to_start_command(
            TmuxClaudeBackend,
            "python custom_wrapper.py",
        )
        is TmuxClaudeBackend
    )
