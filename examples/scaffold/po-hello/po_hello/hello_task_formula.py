"""`hello-task` formula — a @flow dispatched via `po run hello-task`.

Scaffolded by `po new formula`. Follows the PO formula signature convention:
`(issue_id, rig, rig_path, *, parent_bead=None, dry_run=False)`. Verdicts flow
to the orchestrator as files under `$RUN_DIR/verdicts/<step>.json` (or as bd
metadata on dolt rigs — see engdocs/verdict-channel-backends.md). Replace the
body with the real pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prefect import flow


@flow(name="hello-task", flow_run_name="{issue_id}", log_prints=True)
def hello_task(
    issue_id: str,
    rig: str,
    rig_path: str,
    *,
    parent_bead: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """One-line summary of what `hello-task` does.

    Args:
        issue_id: the seed bead this run implements.
        rig: rig slug (usually the rig-path basename).
        rig_path: absolute path to the repo where code lives.
        parent_bead: parent bead id when dispatched as a graph node.
        dry_run: skip side effects; emit a stubbed verdict.
    """
    run_dir = Path(rig_path) / ".planning" / "hello-task" / issue_id
    verdicts = run_dir / "verdicts"
    verdicts.mkdir(parents=True, exist_ok=True)

    if dry_run:
        status = "pass"
        summary = "dry-run: no work performed"
    else:
        # TODO: do the real work here (spawn an AgentSession, run a step, etc.).
        status = "pass"
        summary = "scaffolded formula — replace this body"

    # Verdict-file write example — orchestrator-readable pass/fail artifact.
    (verdicts / "hello_task.json").write_text(
        json.dumps({"status": status, "summary": summary}, indent=2)
    )
    return {"status": status, "issue_id": issue_id, "run_dir": str(run_dir)}
