"""Shared pytest fixtures for the prefect-orchestration unit suite."""

from __future__ import annotations

import pytest

from prefect_orchestration import beads_backend


@pytest.fixture(autouse=True)
def _pin_beads_binary_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `resolve_backend` host-independent in unit tests.

    `resolve_backend` resolves the beads backend in three steps: a
    `PO_BEADS_BACKEND` env override, then a `.beads/metadata.json` sniff, then
    the `_bd_is_really_br()` probe of the on-PATH `bd` binary. Two of those leak
    the host's state into tests after the machine-wide `bd`->`br` migration
    (prefect-orchestration-3d7y, -b6mz):

    - the **probe** reads the real `bd` (now `br`), and
    - the **sniff** reads whatever `.beads/metadata.json` the cwd/rig happens to
      have (this repo's is `br`) — which the probe-patch alone does NOT cover,
      so dolt-format-mock tests still took the `br` path and failed.

    So pin BOTH: default the env override to `dolt` (the historical format the
    unit mocks assume) so neither the sniff nor the probe can hijack a test, and
    patch the probe to `False`. Tests that exercise `br` override
    `PO_BEADS_BACKEND` (`setenv`) or the probe (`lambda: True`) explicitly; a
    test asserting the sniff clears the env with `delenv`.
    """
    monkeypatch.setattr(beads_backend, "_BD_IS_BR_MEMO", None)
    monkeypatch.setattr(beads_backend, "_bd_is_really_br", lambda: False)
    monkeypatch.setenv("PO_BEADS_BACKEND", "dolt")


@pytest.fixture(autouse=True)
def _isolate_packs_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Keep pack tests off the operator's real `~/.config/po/packs.json`.

    The install/update tests record their fixture packs in the manifest, and
    `po packs restore` reinstalls whatever it lists, so a run of the unit suite
    once left `/tmp/pytest-*` and `po-mypack` entries in the real file.
    """
    manifest = tmp_path_factory.mktemp("po-config") / "packs.json"
    monkeypatch.setenv("PO_PACKS_MANIFEST", str(manifest))
