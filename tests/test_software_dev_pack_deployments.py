"""Regression tests for software-dev pack deployment entrypoints."""

from __future__ import annotations

import sys
from pathlib import Path

from prefect.deployments.runner import EntrypointType


REPO_ROOT = Path(__file__).resolve().parents[1]
PACK_ROOT = REPO_ROOT / "packs" / "po-formulas-software-dev"


def test_software_dev_pack_register_uses_module_entrypoints() -> None:
    sys.path.insert(0, str(PACK_ROOT))
    try:
        from po_formulas.deployments import register

        deployments = register()
    finally:
        sys.path.pop(0)

    expected = {
        "epic-sr-8yu-nightly": "po_formulas.epic.epic_run",
        "epic-manual": "po_formulas.epic.epic_run",
        "software-dev-full-manual": "po_formulas.software_dev.software_dev_full",
        "software-dev-fast-manual": "po_formulas.software_dev.software_dev_fast",
        "software-dev-edit-manual": "po_formulas.software_dev.software_dev_edit",
    }

    assert {dep.name: dep.entrypoint for dep in deployments} == expected
    for dep in deployments:
        assert dep.entrypoint_type == EntrypointType.MODULE_PATH
