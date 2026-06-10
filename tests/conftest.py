"""Shared pytest fixtures for the prefect-orchestration unit suite."""

from __future__ import annotations

import pytest

from prefect_orchestration import beads_backend


@pytest.fixture(autouse=True)
def _pin_beads_binary_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `resolve_backend` host-independent in unit tests.

    `resolve_backend` consults `_bd_is_really_br()` (which probes the on-PATH
    `bd` binary) to downgrade a stale dolt sniff to `br` after the machine-wide
    `bd`->`br` migration (prefect-orchestration-3d7y). Unit tests must not depend
    on whether the dev/CI machine happens to have dolt-`bd` or beads-rust on
    PATH, so default the probe to `False` (a genuine dolt `bd`) — preserving the
    historical sniff-only behaviour. The tests that exercise the downgrade
    override this explicitly with `lambda: True`.
    """
    beads_backend._bd_is_really_br.cache_clear()
    monkeypatch.setattr(beads_backend, "_bd_is_really_br", lambda: False)
