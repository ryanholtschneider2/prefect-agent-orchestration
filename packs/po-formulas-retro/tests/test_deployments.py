from __future__ import annotations

import sys
from pathlib import Path

from prefect.deployments.runner import EntrypointType


REPO_ROOT = Path(__file__).resolve().parents[3]
PACK_ROOT = REPO_ROOT / "packs" / "po-formulas-retro"


def test_retro_pack_register_uses_module_entrypoints() -> None:
    sys.path.insert(0, str(PACK_ROOT))
    try:
        from po_formulas_retro.deployments import register

        deployments = register()
    finally:
        sys.path.pop(0)

    expected = {
        "update-prompts-from-lessons-manual": "po_formulas_retro.flows.update_prompts_from_lessons",
        "update-prompts-from-lessons-weekly": "po_formulas_retro.flows.update_prompts_from_lessons",
    }

    assert {deployment.name: deployment.entrypoint for deployment in deployments} == expected
    for deployment in deployments:
        assert deployment.entrypoint_type == EntrypointType.MODULE_PATH

    weekly = next(deployment for deployment in deployments if deployment.name.endswith("weekly"))
    assert weekly.schedules
    assert weekly.schedules[0].schedule.timezone == "America/New_York"
