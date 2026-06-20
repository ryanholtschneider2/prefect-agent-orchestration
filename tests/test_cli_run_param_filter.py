"""`po run` signature-filter + `--param` pass-through (prefect-orchestration-2kaa).

Two defects this covers:

1. `po run` injected CLI-isms (rig, rig_path, …) into the formula call. Formulas
   that don't declare them (e.g. provision_business) raised TypeError on the
   synchronous path / SignatureMismatchError on worker pickup. The fix filters
   kwargs to the flow fn's real signature before dispatch.
2. A formula's own `dry_run` kwarg could never be set because bare `--dry-run`
   is a reserved po-CLI flag. The fix adds a `--param key=value` pass-through.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from typer.testing import CliRunner

from prefect_orchestration.cli import (
    _filter_kwargs_for_flow,
    _merge_param_overrides,
    app,
)


def _patch_flow(
    monkeypatch: pytest.MonkeyPatch, fn: Any, name: str = "my-flow"
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _wrapped(**kwargs: Any) -> str:
        captured["kwargs"] = dict(kwargs)
        return fn(**kwargs)

    _wrapped.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
    monkeypatch.setattr(
        "prefect_orchestration.cli._load_formulas", lambda: {name: _wrapped}
    )
    monkeypatch.setattr(
        "prefect_orchestration.cli._autoconfigure_prefect_api", lambda: None
    )
    return captured


# --- unit: _filter_kwargs_for_flow --------------------------------------


def test_filter_drops_unaccepted_kwargs() -> None:
    def _flow(business: str, execute: bool = False) -> str:
        return "ok"

    out = _filter_kwargs_for_flow(
        _flow,
        {"business": "acme", "execute": True, "rig": "soloco", "rig_path": "/x"},
        label="provision-business",
    )
    assert out == {"business": "acme", "execute": True}


def test_filter_keeps_everything_for_var_keyword_flow() -> None:
    def _flow(issue_id: str, **kwargs: Any) -> str:
        return "ok"

    payload = {"issue_id": "i-1", "rig": "soloco", "rig_path": "/x"}
    assert _filter_kwargs_for_flow(_flow, payload, label="sd") == payload


# --- unit: _merge_param_overrides ---------------------------------------


def test_merge_param_overrides_coerces_and_overrides() -> None:
    kwargs: dict[str, Any] = {"execute": False}
    _merge_param_overrides(kwargs, ["dry_run=false", "execute=true", "name=acme"])
    assert kwargs["dry_run"] is False
    assert kwargs["execute"] is True
    assert kwargs["name"] == "acme"


def test_merge_param_rejects_bare_token() -> None:
    with pytest.raises(Exception):  # typer.BadParameter
        _merge_param_overrides({}, ["dry_run"])


# --- integration: po run end-to-end -------------------------------------


def test_run_filters_rig_for_non_sd_formula(monkeypatch: pytest.MonkeyPatch) -> None:
    """rig/rig_path must NOT reach a formula that doesn't declare them."""

    def _flow(business: str, dry_run: bool = True) -> str:
        return "ok"

    captured = _patch_flow(monkeypatch, _flow)
    result = CliRunner().invoke(
        app,
        ["run", "my-flow", "--business", "acme", "--rig", "soloco", "--rig-path", "/x"],
    )
    assert result.exit_code == 0, result.output
    assert captured["kwargs"] == {"business": "acme"}


def test_run_param_sets_shadowed_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--param dry_run=false` reaches the formula even though --dry-run is reserved."""

    def _flow(business: str, dry_run: bool = True) -> str:
        return "ok"

    captured = _patch_flow(monkeypatch, _flow)
    result = CliRunner().invoke(
        app,
        ["run", "my-flow", "--business", "acme", "--param", "dry_run=false"],
    )
    assert result.exit_code == 0, result.output
    assert captured["kwargs"] == {"business": "acme", "dry_run": False}


def test_reserved_dry_run_still_prints_and_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare --dry-run keeps po's print-and-exit semantics (not the formula's)."""

    def _flow(business: str, dry_run: bool = True) -> str:
        raise AssertionError("formula must not run under --dry-run")

    _patch_flow(monkeypatch, _flow)
    result = CliRunner().invoke(
        app, ["run", "my-flow", "--business", "acme", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert "[dry-run]" in result.output
