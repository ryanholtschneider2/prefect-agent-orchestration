"""Prefect deployments for the example formula pack."""

from __future__ import annotations

from prefect.schedules import Cron

from po_example_formulas.flows import builder_heartbeat, on_bd_close, triage_inbox


def register() -> list:
    return [
        builder_heartbeat.to_deployment(name="builder-heartbeat-manual"),
        builder_heartbeat.to_deployment(
            name="builder-heartbeat-workday",
            schedule=Cron("*/30 9-17 * * 1-5", timezone="America/New_York"),
            parameters={"role": "builder"},
        ),
        triage_inbox.to_deployment(
            name="triage-inbox-daily",
            schedule=Cron("0 8 * * *", timezone="America/New_York"),
        ),
        triage_inbox.to_deployment(name="triage-inbox-manual"),
        on_bd_close.to_deployment(name="on-bd-close-manual"),
    ]
