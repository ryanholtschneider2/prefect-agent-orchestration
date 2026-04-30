"""Unit tests for `po_stripe.commands`.

All subprocess + PATH calls are mocked. No live Stripe calls.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from po_stripe import commands


_FAKE_STRIPE = "/usr/bin/stripe"


def _fake_run(stdout: object, returncode: int = 0):
    payload = json.dumps(stdout) if not isinstance(stdout, str) else stdout

    def _runner(argv, **kw):  # noqa: ARG001
        return subprocess.CompletedProcess(argv, returncode, stdout=payload, stderr="")

    return _runner


# ---------- balance ----------


def test_balance_prints_available_and_pending(monkeypatch, capsys):
    monkeypatch.setattr(commands.shutil, "which", lambda _: _FAKE_STRIPE)
    monkeypatch.setattr(
        commands.subprocess,
        "run",
        _fake_run(
            {
                "available": [{"amount": 12345, "currency": "USD"}],
                "pending": [{"amount": 678, "currency": "usd"}],
            }
        ),
    )

    commands.balance()
    out = capsys.readouterr().out
    assert "available" in out and "123.45" in out and "usd" in out
    assert "pending" in out and "6.78" in out


def test_balance_missing_cli_exits_with_doctor_hint(monkeypatch, capsys):
    monkeypatch.setattr(commands.shutil, "which", lambda _: None)
    with pytest.raises(SystemExit) as exc:
        commands.balance()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "po doctor" in err


# ---------- recent_charges ----------


def test_recent_charges_tabulates(monkeypatch, capsys):
    monkeypatch.setattr(commands.shutil, "which", lambda _: _FAKE_STRIPE)
    monkeypatch.setattr(
        commands.subprocess,
        "run",
        _fake_run(
            {
                "data": [
                    {
                        "id": "ch_111",
                        "amount": 5000,
                        "currency": "usd",
                        "status": "succeeded",
                        "created": 1700000000,
                        "customer": "cus_abc",
                    },
                    {
                        "id": "ch_222",
                        "amount": 100,
                        "currency": "usd",
                        "status": "failed",
                        "created": 1700000050,
                        "customer": None,
                    },
                ]
            }
        ),
    )

    commands.recent_charges(limit=5)
    out = capsys.readouterr().out
    assert "ch_111" in out and "ch_222" in out
    assert "succeeded" in out and "failed" in out
    assert "50.00" in out and "1.00" in out
    assert "cus_abc" in out
    # ISO-formatted UTC timestamp
    assert "2023-11-" in out


def test_recent_charges_rejects_bad_limit(monkeypatch):
    monkeypatch.setattr(commands.shutil, "which", lambda _: _FAKE_STRIPE)
    with pytest.raises(SystemExit):
        commands.recent_charges(limit=0)
    with pytest.raises(SystemExit):
        commands.recent_charges(limit=101)


def test_recent_charges_propagates_cli_failure(monkeypatch, capsys):
    monkeypatch.setattr(commands.shutil, "which", lambda _: _FAKE_STRIPE)
    monkeypatch.setattr(
        commands.subprocess,
        "run",
        _fake_run("auth error", returncode=1),
    )
    with pytest.raises(SystemExit) as exc:
        commands.recent_charges(limit=3)
    assert exc.value.code == 2
    assert "failed" in capsys.readouterr().err


def test_recent_charges_timeout_is_clean(monkeypatch, capsys):
    monkeypatch.setattr(commands.shutil, "which", lambda _: _FAKE_STRIPE)

    def _raise(*a, **kw):  # noqa: ARG001
        raise subprocess.TimeoutExpired(cmd="stripe", timeout=10)

    monkeypatch.setattr(commands.subprocess, "run", _raise)
    with pytest.raises(SystemExit):
        commands.recent_charges(limit=3)
    assert "timed out" in capsys.readouterr().err


# ---------- mode ----------


def test_mode_test_key(monkeypatch, capsys):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_abcdefghijklmnop")
    commands.mode()
    out = capsys.readouterr().out
    assert "test" in out and "sk_test_" in out
    # full key never printed
    assert "abcdefghijklmnop" not in out


def test_mode_live_key(monkeypatch, capsys):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_live_secrettail12345")
    commands.mode()
    out = capsys.readouterr().out
    assert "live" in out
    assert "secrettail12345" not in out


def test_mode_unknown(monkeypatch, capsys):
    monkeypatch.setenv("STRIPE_API_KEY", "rk_random_garbage_456789")
    commands.mode()
    out = capsys.readouterr().out
    assert "unknown" in out
    assert "garbage_456789" not in out


def test_mode_unset(monkeypatch, capsys):
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    commands.mode()
    assert "unset" in capsys.readouterr().out
