"""po_formulas — formula packs built on top of prefect-orchestration core.

This package currently hosts only the agent-messaging helper (`mail`).
Concrete formulas (`software_dev`, `epic`, `deployments`) are referenced
as entry points from `prefect_orchestration.cli` / `deployments` but have
not yet landed in-tree; importing `po_formulas.software_dev` etc. will
still fail at runtime until those modules ship. Mail does not depend on
any of them.
"""
