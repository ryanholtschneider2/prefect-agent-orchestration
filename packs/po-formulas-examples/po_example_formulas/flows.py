"""Runnable example flows for pack authors to copy and adapt."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from prefect import flow
from prefect_orchestration.agent_session import AgentSession, ClaudeCliBackend, StubBackend
from prefect_orchestration.role_sessions import RoleSessionStore

from po_example_formulas.state import ExampleState


def _session_store(rig_path: Path, role: str) -> RoleSessionStore:
    run_dir = rig_path / ".planning" / "po-formulas-examples" / f"{role}-heartbeat"
    return RoleSessionStore(
        seed_id=f"example-{role}-heartbeat",
        seed_run_dir=run_dir,
    )


def _pick_builder_work(
    messages: list[dict[str, Any]], ready: list[dict[str, Any]]
) -> tuple[str, dict[str, Any]] | None:
    if messages:
        return "mail", messages[0]
    if ready:
        return "ready", ready[0]
    return None


def _classify_message(message: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    hint = str(message.get("route_hint") or "").strip()
    if hint:
        return hint, {}
    haystack = " ".join(
        str(message.get(key, "")) for key in ("subject", "body", "sender")
    ).lower()
    if any(word in haystack for word in ("bug", "fix", "error", "issue", "todo")):
        return "create_bead", {}
    if any(word in haystack for word in ("invoice", "billing", "payment")):
        return "dispatch_formula", {"target_formula": "invoice-reconcile"}
    if any(word in haystack for word in ("reply", "respond")):
        return "draft_reply", {}
    return "ignore", {}


def _message_id(message: dict[str, Any], fallback: str) -> str:
    return str(message.get("message_id") or fallback)


@flow(name="builder_heartbeat", flow_run_name="{role}-heartbeat", log_prints=True)
def builder_heartbeat(
    rig_path: str,
    role: str = "builder",
    dry_run: bool = False,
    model: str = "opus",
    effort: str | None = None,
) -> dict[str, Any]:
    """Role-specific standing order over a dummy rig's local state.

    The flow checks role mail plus the ready queue under `.po-example/`,
    resumes a stable AgentSession for the role, prompts it to make one
    bounded step, then records the turn in a rig-local JSONL log.
    """
    rig = Path(rig_path).expanduser().resolve()
    state = ExampleState(rig)
    state.ensure()
    messages = state.load_role_mail(role)
    ready = state.load_ready(role)
    picked = _pick_builder_work(messages, ready)
    if picked is None:
        return {"status": "idle", "role": role, "message_count": 0, "ready_count": 0}

    source, item = picked
    if source == "mail":
        remaining_mail = messages[1:]
        state.save_role_mail(role, remaining_mail)
        bead_id = str(item.get("bead_id") or item.get("id") or "mail-task")
        goal = str(item.get("subject") or item.get("goal") or "Process message")
    else:
        remaining_ready = ready[1:]
        state.save_ready(role, remaining_ready)
        bead_id = str(item.get("bead_id") or "ready-task")
        goal = str(item.get("goal") or item.get("title") or "Do queued work")

    prompt = (
        f"Heartbeat role: {role}\n"
        f"Source: {source}\n"
        f"Bead: {bead_id}\n"
        f"Goal: {goal}\n\n"
        "Take one bounded step and leave the repo in a better state."
    )
    state.last_prompt_path(role).parent.mkdir(parents=True, exist_ok=True)
    state.last_prompt_path(role).write_text(prompt)

    store = _session_store(rig, role)
    backend = StubBackend() if dry_run else ClaudeCliBackend()
    sess = AgentSession(
        role=role,
        repo_path=rig,
        backend=backend,
        session_id=store.get(role),
        model=model,
        effort=effort,
        issue_id=bead_id,
        overlay=False,
        skills=False,
    )
    output = sess.prompt(prompt)
    if sess.session_id:
        store.set(role, sess.session_id)

    state.append_jsonl(
        state.run_log_path("heartbeat-runs"),
        {
            "role": role,
            "source": source,
            "bead_id": bead_id,
            "goal": goal,
            "session_id": sess.session_id,
            "output": output,
        },
    )
    return {
        "status": "worked",
        "role": role,
        "source": source,
        "bead_id": bead_id,
        "session_id": sess.session_id,
        "output": output,
    }


@flow(name="triage_inbox", flow_run_name="{account}-triage", log_prints=True)
def triage_inbox(
    rig_path: str,
    account: str = "default",
    limit: int = 25,
    target_role: str = "builder",
) -> dict[str, Any]:
    """Route inbox items from `.po-example/inbox/<account>/untriaged/`.

    For the dummy rig we use JSON files as inbound messages. The flow
    classifies each message, writes its side effect (bead, draft, or
    dispatch record), archives the message into `triaged/`, and returns
    route counts.
    """
    rig = Path(rig_path).expanduser().resolve()
    state = ExampleState(rig)
    state.ensure()
    counts: dict[str, int] = {}
    processed = 0
    for path, message in state.list_untriaged(account, limit):
        route, extra = _classify_message(message)
        counts[route] = counts.get(route, 0) + 1
        message_key = path.stem
        msg_id = _message_id(message, message_key)

        if route == "create_bead":
            bead_id = str(message.get("bead_id") or f"inbox-{message_key}")
            bead = {
                "id": bead_id,
                "status": "open",
                "source_message_id": msg_id,
                "title": message.get("subject") or bead_id,
                "labels": message.get("labels", ["intake"]),
                "metadata": {
                    "target_role": message.get("target_role", target_role),
                    "follow_on_formula": message.get("follow_on_formula"),
                },
            }
            state.write_bead(bead_id, bead)
            ready = state.load_ready(str(bead["metadata"]["target_role"]))
            ready.append(
                {
                    "bead_id": bead_id,
                    "goal": message.get("goal") or message.get("subject") or bead_id,
                    "source_message_id": msg_id,
                }
            )
            state.save_ready(str(bead["metadata"]["target_role"]), ready)
        elif route == "draft_reply":
            draft_path = state.drafts_dir() / f"{msg_id}.md"
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text(
                f"# Draft reply for {msg_id}\n\nReply to: {message.get('sender', 'unknown')}\n"
            )
        elif route == "dispatch_formula":
            state.append_jsonl(
                state.run_log_path("dispatches"),
                {
                    "source": "triage-inbox",
                    "message_id": msg_id,
                    "formula": extra.get("target_formula")
                    or message.get("target_formula")
                    or "follow-up",
                },
            )

        archived = dict(message)
        archived["route"] = route
        archived["processed_by"] = "triage-inbox"
        state.archive_triaged(account, path, archived)
        processed += 1

    return {"status": "ok", "account": account, "processed": processed, "counts": counts}


@flow(name="on_bd_close", flow_run_name="{bead_id}", log_prints=True)
def on_bd_close(
    rig_path: str,
    bead_id: str,
) -> dict[str, Any]:
    """Follow-on trigger for a closed bead in the dummy rig.

    Reads the bead record under `.po-example/beads/<id>.json`. If the
    bead is closed and names a `follow_on_formula`, the flow records a
    dispatch event to `.po-example/dispatches.jsonl`.
    """
    rig = Path(rig_path).expanduser().resolve()
    state = ExampleState(rig)
    state.ensure()
    bead = state.read_bead(bead_id)
    if bead is None:
        return {"status": "ignored", "reason": "missing-bead", "bead_id": bead_id}
    if bead.get("status") != "closed":
        return {"status": "ignored", "reason": "not-closed", "bead_id": bead_id}

    metadata = bead.get("metadata") or {}
    follow_on = metadata.get("follow_on_formula")
    if not follow_on:
        return {"status": "ignored", "reason": "no-follow-on", "bead_id": bead_id}

    payload = {
        "source": "on-bd-close",
        "bead_id": bead_id,
        "formula": follow_on,
        "labels": bead.get("labels", []),
    }
    state.append_jsonl(state.run_log_path("dispatches"), payload)
    return {"status": "triggered", "bead_id": bead_id, "formula": str(follow_on)}
