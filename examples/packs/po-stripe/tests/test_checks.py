"""Unit tests for `po_stripe.checks`."""

from __future__ import annotations

import subprocess

import pytest

from po_stripe import checks


_FAKE_STRIPE = "/usr/bin/stripe"


def _ok(stdout: str = "stripe 1.21.0"):
    def _runner(argv, **kw):  # noqa: ARG001
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    return _runner


def _exit(code: int = 1, stdout: str = "", stderr: str = "boom"):
    def _runner(argv, **kw):  # noqa: ARG001
        return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr=stderr)

    return _runner


# ---------- cli_installed ----------


def test_cli_installed_missing(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda _: None)
    out = checks.cli_installed()
    assert out.status == "red"
    assert "brew install stripe" in (out.hint or "")


def test_cli_installed_present(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda _: _FAKE_STRIPE)
    monkeypatch.setattr(checks.subprocess, "run", _ok("stripe 1.21.0\n"))
    out = checks.cli_installed()
    assert out.status == "green"
    assert "stripe" in out.message


def test_cli_installed_nonzero(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda _: _FAKE_STRIPE)
    monkeypatch.setattr(checks.subprocess, "run", _exit(7))
    out = checks.cli_installed()
    assert out.status == "red"


def test_cli_installed_timeout(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda _: _FAKE_STRIPE)

    def _raise(*a, **kw):  # noqa: ARG001
        raise subprocess.TimeoutExpired(cmd="stripe", timeout=4)

    monkeypatch.setattr(checks.subprocess, "run", _raise)
    out = checks.cli_installed()
    assert out.status == "yellow"


# ---------- env_set ----------


@pytest.mark.parametrize(
    ("key", "po_env", "expected"),
    [
        (None, None, "red"),
        ("", None, "red"),
        ("rk_garbage", None, "red"),
        ("sk_test_abcdefghij", None, "green"),
        ("sk_live_abcdefghij", None, "yellow"),
        ("sk_live_abcdefghij", "prod", "green"),
        ("sk_test_abcdefghij", "prod", "yellow"),
        ("sk_live_abcdefghij", "PROD", "green"),
    ],
)
def test_env_set_matrix(monkeypatch, key, po_env, expected):
    if key is None:
        monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    else:
        monkeypatch.setenv("STRIPE_API_KEY", key)
    if po_env is None:
        monkeypatch.delenv("PO_ENV", raising=False)
    else:
        monkeypatch.setenv("PO_ENV", po_env)

    out = checks.env_set()
    assert out.status == expected
    # Full key never echoed (we slice [:8] + "…")
    if key:
        assert key not in (out.message or "")
        assert key not in (out.hint or "")


# ---------- api_reachable ----------


def test_api_reachable_env_unset(monkeypatch):
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    # subprocess shouldn't be called; assert by replacing with a raiser
    monkeypatch.setattr(
        checks.subprocess, "run", lambda *a, **kw: pytest.fail("called")
    )
    out = checks.api_reachable()
    assert out.status == "yellow"
    assert "skipping" in (out.message or "").lower()


def test_api_reachable_cli_missing(monkeypatch):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_x")
    monkeypatch.setattr(checks.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        checks.subprocess, "run", lambda *a, **kw: pytest.fail("called")
    )
    out = checks.api_reachable()
    assert out.status == "yellow"


def test_api_reachable_ok(monkeypatch):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_x")
    monkeypatch.setattr(checks.shutil, "which", lambda _: _FAKE_STRIPE)
    monkeypatch.setattr(checks.subprocess, "run", _ok('{"available":[]}'))
    out = checks.api_reachable()
    assert out.status == "green"


def test_api_reachable_nonzero(monkeypatch):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_x")
    monkeypatch.setattr(checks.shutil, "which", lambda _: _FAKE_STRIPE)
    monkeypatch.setattr(checks.subprocess, "run", _exit(1, stderr="bad key"))
    out = checks.api_reachable()
    assert out.status == "yellow"
    assert "bad key" in (out.message or "")


def test_api_reachable_timeout(monkeypatch):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_x")
    monkeypatch.setattr(checks.shutil, "which", lambda _: _FAKE_STRIPE)

    def _raise(*a, **kw):  # noqa: ARG001
        raise subprocess.TimeoutExpired(cmd="stripe", timeout=5)

    monkeypatch.setattr(checks.subprocess, "run", _raise)
    out = checks.api_reachable()
    assert out.status == "yellow"


def test_api_reachable_oserror(monkeypatch):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_x")
    monkeypatch.setattr(checks.shutil, "which", lambda _: _FAKE_STRIPE)

    def _raise(*a, **kw):  # noqa: ARG001
        raise OSError("permission denied")

    monkeypatch.setattr(checks.subprocess, "run", _raise)
    out = checks.api_reachable()
    assert out.status == "red"
