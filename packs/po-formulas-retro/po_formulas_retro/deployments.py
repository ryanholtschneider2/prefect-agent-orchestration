"""Prefect deployments for the retro formula pack."""

from __future__ import annotations

from prefect.deployments.runner import EntrypointType
from prefect.schedules import Cron

from po_formulas_retro.flows import update_prompts_from_lessons


def register() -> list:
    module_path = {"entrypoint_type": EntrypointType.MODULE_PATH}
    return [
        update_prompts_from_lessons.to_deployment(
            name="update-prompts-from-lessons-manual",
            **module_path,
        ),
        update_prompts_from_lessons.to_deployment(
            name="update-prompts-from-lessons-weekly",
            schedule=Cron("0 9 * * 1", timezone="America/New_York"),
            **module_path,
        ),
    ]
