"""`agent_step` — the simplified primitive for running ONE agent turn against a bead.

Replaces the graph-mode `per_role_step` machinery for use cases that don't
need a reactive bead-graph dispatcher. Pack authors write plain Prefect
`@task` / `@flow` code and call `agent_step(...)` for each agent dispatch;
all the messy parts (bead-stamping, session affinity, convergence ladder,
verdict parsing) live here.

# Mental model

A bead is a unit of work. An agent dispatched against a bead reads the
bead's description for the task spec, does the work, then `bd close`s the
bead with a reason whose keyword encodes the verdict. `agent_step`:

1. **Resolves the target bead.** When `iter_n` is set, creates / reuses
   `<seed>.<step>.iter<N>` (idempotent — if the child bead already exists,
   reuse it without erroring). Otherwise the seed itself IS the target.
2. **Resumability.** If the target bead is already closed, parses its
   close-reason and returns the verdict without running the agent. Re-runs
   of a partially-completed pipeline skip already-done work.
3. **Stamps the task spec.** Renders the supplied task content (`Path` →
   read file, `str` → use as-is) with `ctx` substitutions and writes it
   as the bead's description via `bd update --description=…`. The agent's
   first action (`bd show <bead>`) returns the canonical task for THIS
   step.
4. **Renders the agent identity prompt.** Reads `<agent_dir>/prompt.md`
   (small, stable: "you are X, read your bead, close per the contract") +
   substitutions including `{{role_step_bead_id}}`.
5. **Resumes the persistent session.** Looks up the role's `--resume <uuid>`
   in `RoleSessionStore` keyed on `(seed_id, role)`. The builder's iter2
   continues iter1's conversation; the critic reviewing the same parent
   keeps its scratch.
6. **Runs the turn.**
7. **Convergence ladder.** Bead closed by the agent → parse verdict →
   return. Bead still open → ONE nudge turn ("close now / `bd human`
   if blocked / finish + close") → re-check. Still open → defensive
   force-close so callers don't busy-loop.

# Usage shape

```python
from prefect import task, flow
from prefect_orchestration import agent_step
from pathlib import Path

AGENTS = Path(__file__).parent / "agents"
TASKS = Path(__file__).parent / "my_formula"

@task
def triage(seed_id, rig_path):
    return agent_step(
        agent_dir=AGENTS / "triager",
        task=TASKS / "triage.md",
        seed_id=seed_id, rig_path=rig_path,
    )

@task
def plan(seed_id, rig_path, iter_n):
    return agent_step(
        agent_dir=AGENTS / "planner",
        task=TASKS / "plan.md",
        seed_id=seed_id, rig_path=rig_path,
        iter_n=iter_n, step="plan",
        ctx={"prior_critique": _load_critique(seed_id, iter_n - 1)},
    )

@task
def plan_critic(seed_id, rig_path, iter_n):
    return agent_step(
        agent_dir=AGENTS / "critic",
        task=TASKS / "plan-critic.md",
        seed_id=seed_id, rig_path=rig_path,
        iter_n=iter_n, step="plan-critic",
        verdict_keywords=("approved", "rejected"),
    )

@flow
def my_pipeline(issue_id, rig, rig_path):
    triage(issue_id, rig_path)
    for i in range(1, 3):
        plan(issue_id, rig_path, iter_n=i)
        v = plan_critic(issue_id, rig_path, iter_n=i)
        if v.get("verdict") == "approved":
            break
    # ...
```
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prefect_orchestration.agent_session import AgentSession, StubBackend
from prefect_orchestration.backend_select import select_default_backend
from prefect_orchestration.beads_meta import (
    _bd_available,
    _bd_show,
    close_issue,
    create_child_bead,
)
from prefect_orchestration.role_config import resolve_role_runtime
from prefect_orchestration.role_sessions import RoleSessionStore
from prefect_orchestration.templates import render_template


# Substitution applied to BOTH the agent identity prompt and the task
# spec when caller-supplied `ctx` doesn't provide a key. Keeps prompts
# referencing `{{seed_id}}`, `{{role_step_bead_id}}`, etc. rendering
# cleanly without packs needing to manually populate every var.
def _safe_substitute(template: str, vars_: dict[str, Any]) -> str:
    pattern = re.compile(r"\{\{\s*(\w+)\s*\}\}")
    return pattern.sub(lambda m: str(vars_.get(m.group(1), m.group(0))), template)


@dataclass
class AgentStepResult:
    """Return shape for `agent_step`.

    `verdict`/`summary` are populated when `verdict_keywords` was set
    AND the bead's close-reason matched a keyword. `reply` is the agent's
    raw last reply text (useful when no verdict parsing is requested).
    `bead_id` is the bead the agent operated on (seed or `<seed>.<step>.iter<N>`).
    `from_cache` is True when the bead was already closed at entry and the
    agent did NOT run (resumability path).
    """

    bead_id: str
    verdict: str = ""
    summary: str = ""
    reply: str = ""
    from_cache: bool = False
    closed_by: str = ""  # "agent" | "nudge" | "force" | "cache"

    def __getitem__(self, key: str) -> Any:
        # dict-like access for ergonomic check `if result["verdict"] == "approved"`
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


def agent_step(
    *,
    agent_dir: Path,
    task: Path | str | None = None,
    seed_id: str,
    rig_path: str,
    run_dir: Path | str | None = None,
    ctx: dict[str, Any] | None = None,
    iter_n: int | None = None,
    step: str | None = None,
    verdict_keywords: tuple[str, ...] = (),
    session_role: str | None = None,
    backend: Any = None,
    dry_run: bool = False,
) -> AgentStepResult:
    """Run ONE agent turn against a bead. See module docstring for design.

    Parameters
    ----------
    agent_dir
        Filesystem path to the agent's role directory. Must contain
        `prompt.md` (identity + close-contract template). Identity is
        resolved via `render_template`'s standard rules (optional
        `identity.toml`, `memory/MEMORY.md` overlay).
    task
        Task spec for THIS step. `Path` → read file. `str` → use as
        inline content. `None` → leave the bead's description as-is
        (caller has already set it, e.g. via `bd create --description`).
        After substitution with `ctx`, this content is stamped onto the
        bead's description so the agent's `bd show` returns it.
    seed_id
        The "real" issue bead. Persistent session UUIDs live keyed
        against this id (so build-iter2 resumes build-iter1's session).
    rig_path
        Where `bd` resolves `.beads/` from.
    ctx
        Extra template variables for the agent prompt and task spec.
        Always-present vars (caller-overrideable):
        ``seed_id``, ``rig_path``, ``role_step_bead_id``, ``iter``,
        ``step``, ``run_dir`` (when caller supplies it).
    iter_n
        When set, operate on `<seed_id>.<step>.iter<N>`; create if absent.
        When unset, the seed bead itself is the target.
    step
        Bead name segment for the child iter bead. Required when
        `iter_n` is set; ignored otherwise. Conventional: short
        kebab-case (`plan`, `build`, `plan-critic`, `lint`).
    verdict_keywords
        Tuple of close-reason keywords to recognize. When the bead's
        close-reason starts with `<keyword>:` (or contains the keyword
        as a token), `verdict` is set to that keyword. When empty, no
        verdict parsing is done — caller gets `reply` and the raw
        close-reason via `bead_id` lookup.
    session_role
        Override the role key used by `RoleSessionStore`. Default:
        the basename of `agent_dir` (i.e. `agents/builder` → "builder").
        Roles that should share a session (e.g. all critics) can pass
        a common name.
    backend
        Override the agent backend (`ClaudeCliBackend`,
        `TmuxClaudeBackend`, `StubBackend`). Default: picked by
        `select_default_backend(dry_run)`.
    dry_run
        When True, force `StubBackend` regardless of `backend`.

    Returns
    -------
    AgentStepResult — see class docstring.
    """
    ctx = dict(ctx or {})
    role = session_role or Path(agent_dir).name

    # FAST PATH: compute target bead id from naming convention (zero
    # shellouts) and check status FIRST. If the bead is already closed,
    # return cached verdict — skip every other side effect (run-dir
    # mkdir, metadata stamping, child-bead probe). This collapses 4
    # bd shellouts per cached call to 1, taking cache-hit time from
    # ~10s/step down to ~0.5s/step.
    if iter_n is None:
        target_bead = seed_id
    elif step:
        target_bead = f"{seed_id}.{step}.iter{iter_n}"
    else:
        raise ValueError(f"agent_step: iter_n={iter_n} requires step= (role={role!r})")

    closed_state = _read_bead_status(target_bead, rig_path)
    if closed_state and closed_state["status"] == "closed":
        return _result_from_closed_bead(
            target_bead,
            closed_state,
            verdict_keywords,
            closed_by="cache",
            from_cache=True,
        )

    # SLOW PATH: bead is open or missing — do full setup.
    # Resolve run_dir: caller-supplied wins; else fall through to
    # `<rig>/.planning/agent-step/<seed>/`. The caller-supplied path is
    # the formula's canonical run-dir (e.g.
    # `<rig>/.planning/software-dev-full/<seed>/`), shared across every
    # agent_step call in that formula run so role-sessions.json and
    # verdicts/ all land in one place — and `po artifacts` / `po watch`
    # find them via the seed bead's `po.run_dir` metadata stamp.
    rig_path_p = Path(rig_path).expanduser().resolve()
    if run_dir is not None:
        run_dir_p = Path(run_dir).expanduser().resolve()
    else:
        run_dir_p = rig_path_p / ".planning" / "agent-step" / seed_id
    run_dir_p.mkdir(parents=True, exist_ok=True)
    _stamp_run_dir_meta(seed_id, rig_path_p, run_dir_p)

    # Ensure the iter bead exists (create if absent). Probe-then-create
    # avoids the bd 1.0 resurrection footgun where `bd create --id=<closed>`
    # re-opens the bead.
    if iter_n is not None and closed_state is None and _bd_available():
        create_child_bead(
            seed_id,
            target_bead,
            title=f"{step} iter {iter_n} for {seed_id}",
            description=f"Auto-created by agent_step ({role}, iter {iter_n}).",
            rig_path=rig_path,
        )

    # Stamp the task spec onto the bead description (canonical task source).
    full_ctx: dict[str, Any] = {
        "seed_id": seed_id,
        "rig_path": rig_path,
        "run_dir": str(run_dir_p),
        "role_step_bead_id": target_bead,
        "iter": iter_n if iter_n is not None else "",
        "step": step or "",
        **ctx,
    }
    if task is not None:
        rendered_task = _render_task(task, full_ctx)
        _stamp_description(target_bead, rendered_task, rig_path)

    # Resolve session and run the agent's turn.
    sess = _build_session(
        seed_id=seed_id,
        role=role,
        rig_path=rig_path,
        agent_dir=agent_dir,
        run_dir=run_dir_p,
        backend=backend,
        dry_run=dry_run,
    )
    # Token-efficient resumed-session prompt: when there's a prior
    # session uuid (--resume), the agent already has the identity
    # prompt + role contract from turn 1's conversation. Send only
    # the task-pointer ("your next role-step bead is X — read it,
    # do the work, close per the contract") instead of re-sending
    # the full identity prompt.md every turn.
    is_resumed = bool(sess.session_id)
    if is_resumed:
        rendered_prompt = _render_resumed_prompt(target_bead, full_ctx)
    else:
        rendered_prompt = _render_agent_prompt(agent_dir, full_ctx)

    try:
        reply = sess.prompt(rendered_prompt)
    except Exception:
        # Agent turn failed (rate-limit, transport error, etc.). Don't
        # force-close the bead here — let the caller decide. Re-raise
        # so Prefect captures the failure state.
        raise
    finally:
        # Persist the session UUID after the turn (whether it succeeded
        # or failed) so the next agent_step call for this role resumes
        # the same Claude conversation.
        _persist_session(sess, role)

    # Convergence ladder: agent close → nudge → force-close.
    state = _read_bead_status(target_bead, rig_path)
    if state and state["status"] == "closed":
        return _result_from_closed_bead(
            target_bead,
            state,
            verdict_keywords,
            closed_by="agent",
            reply=reply,
        )

    # Nudge: ONE more turn via the same --resume session.
    nudge_text = _build_nudge_prompt(target_bead, verdict_keywords)
    try:
        sess.prompt(nudge_text)
    except Exception:
        # Nudge failed → fall through to defensive force-close.
        pass
    finally:
        _persist_session(sess, role)

    state = _read_bead_status(target_bead, rig_path)
    if state and state["status"] == "closed":
        return _result_from_closed_bead(
            target_bead,
            state,
            verdict_keywords,
            closed_by="nudge",
            reply=reply,
        )

    # Defensive force-close so the caller's loop converges.
    close_issue(
        target_bead,
        notes="agent did not close bead (nudge failed)",
        rig_path=rig_path,
    )
    return AgentStepResult(
        bead_id=target_bead,
        verdict="failed",
        summary="agent did not close bead (nudge failed)",
        reply=reply,
        closed_by="force",
    )


# ─────────────────────── helpers ────────────────────────────────────


def _read_bead_status(bead_id: str, rig_path: str) -> dict[str, Any] | None:
    if not _bd_available():
        return None
    row = _bd_show(bead_id, rig_path=rig_path)
    if not row:
        return None
    return {
        "status": (row.get("status") or "open").lower(),
        "closure_reason": row.get("closure_reason") or row.get("reason") or "",
        "notes": row.get("notes") or "",
    }


def _result_from_closed_bead(
    bead_id: str,
    state: dict[str, Any],
    verdict_keywords: tuple[str, ...],
    *,
    closed_by: str,
    reply: str = "",
    from_cache: bool = False,
) -> AgentStepResult:
    """Parse a closed bead's close-reason into a structured verdict."""
    reason = state["closure_reason"]
    notes = state["notes"]
    summary = notes.strip() or reason.strip()
    first_line = next((ln for ln in summary.splitlines() if ln.strip()), summary)
    verdict = ""
    if verdict_keywords:
        reason_l = reason.lower()
        notes_l = notes.lower()
        for kw in verdict_keywords:
            if kw.lower() in reason_l or kw.lower() in notes_l:
                verdict = kw
                break
    if not verdict:
        # No keyword match → infer "complete" if any reason set, else empty.
        verdict = "complete" if reason or notes else ""
    return AgentStepResult(
        bead_id=bead_id,
        verdict=verdict,
        summary=first_line.strip(),
        reply=reply,
        from_cache=from_cache,
        closed_by=closed_by,
    )


def _render_task(task: Path | str, ctx: dict[str, Any]) -> str:
    """Read task file (or use inline) and substitute `{{var}}` from ctx.

    Permissive: unknown vars stay literal so packs can inline `{{ITER_N}}`
    or other shell-style placeholders without erroring.
    """
    body = Path(task).read_text() if isinstance(task, Path) else task
    return _safe_substitute(body, ctx)


def _stamp_description(bead_id: str, description: str, rig_path: str) -> None:
    """`bd update <bead> --description=<text>`. Best-effort; warnings only."""
    if not _bd_available():
        return
    subprocess.run(
        ["bd", "update", bead_id, "--description", description],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(rig_path),
    )


def _stamp_run_dir_meta(seed_id: str, rig_path: Path, run_dir: Path) -> None:
    """Stamp `po.run_dir` + `po.rig_path` on the seed bead so the TUI
    (`po artifacts`, `po watch`, `po sessions`, `po retry`) can find the
    formula's canonical run-dir without knowing the formula name.

    Idempotent: re-stamps on every call so a re-run with a different
    run_dir overrides the prior value. Best-effort; bd metadata write
    failures are logged but don't fail the agent step.
    """
    if not _bd_available():
        return
    for key, val in (("po.run_dir", str(run_dir)), ("po.rig_path", str(rig_path))):
        subprocess.run(
            ["bd", "update", seed_id, "--set-metadata", f"{key}={val}"],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(rig_path),
        )


def _build_session(
    *,
    seed_id: str,
    role: str,
    rig_path: str,
    agent_dir: Path,
    run_dir: Path,
    backend: Any,
    dry_run: bool,
) -> AgentSession:
    """Build an `AgentSession` with `--resume <uuid>` from RoleSessionStore.

    The store is keyed on `seed_id`, so successive `agent_step` calls for
    the same role on the same issue resume the same Claude conversation.
    `run_dir` is whatever the caller passed to `agent_step` (or the
    `<rig>/.planning/agent-step/<seed>/` default).
    """
    backend_factory = backend
    if backend_factory is None:
        backend_factory = StubBackend if dry_run else select_default_backend()

    rig_path_p = Path(rig_path).expanduser().resolve()
    sessions = RoleSessionStore(
        seed_id=seed_id, seed_run_dir=run_dir, rig_path=rig_path_p
    )
    prior_uuid = sessions.get(role)

    # Resolve the per-role runtime knobs: per-role config.toml > CLI flag
    # (PO_*_CLI env) > shell env (PO_*) > None. Backends fall back to their
    # hardcoded defaults when a knob is None.
    runtime = resolve_role_runtime(agent_dir)
    backend_kwargs: dict[str, Any] = {}
    if runtime.start_command is not None:
        backend_kwargs["start_command"] = runtime.start_command

    # Different backends have different __init__ shapes:
    #   * ClaudeCliBackend / StubBackend → no required args
    #   * TmuxClaudeBackend → requires issue + role
    # Try the issue+role shape first (works for tmux); fall through to
    # zero-arg construction (works for cli / stub).
    try:
        backend_inst = backend_factory(issue=seed_id, role=role, **backend_kwargs)
    except TypeError:
        try:
            backend_inst = backend_factory(**backend_kwargs)
        except TypeError:
            # Backend doesn't accept start_command (e.g. some stubs); ignore the
            # override rather than crashing the whole step.
            backend_inst = backend_factory()
    session_kwargs: dict[str, Any] = {
        "backend": backend_inst,
        "repo_path": rig_path_p,
        "session_id": prior_uuid,
        "role": role,
        "issue_id": seed_id,
    }
    if runtime.model is not None:
        session_kwargs["model"] = runtime.model
    if runtime.effort is not None:
        session_kwargs["effort"] = runtime.effort
    sess = AgentSession(**session_kwargs)
    # Stash the session store + run_dir on the session for any callers
    # that want to persist the post-turn UUID externally. AgentSession
    # itself doesn't manage persistence — that's the caller's job.
    sess._agent_step_sessions = sessions  # type: ignore[attr-defined]
    sess._agent_step_run_dir = run_dir  # type: ignore[attr-defined]
    return sess


def _render_resumed_prompt(target_bead: str, ctx: dict[str, Any]) -> str:
    """Short prompt for `--resume <uuid>` turns: skip the identity preamble.

    The agent already has the identity prompt + close-contract from
    turn 1's conversation. On resumed turns we only need to point at
    the new role-step bead and re-affirm the close contract; everything
    else is inferred from the prior conversation.

    Saves ~15 lines × tokens-per-line on every iter2+ call (e.g.
    plan-iter-2's planner call resumes plan-iter-1's session). Across
    a long run with multiple plan / build / verify iters this is
    meaningful cost reduction.
    """
    close_block = ctx.get("role_step_close_block", "")
    return (
        f"Your next role-step bead is `{target_bead}`.\n\n"
        "Read it now:\n\n"
        f"```bash\nbd show {target_bead}\n```\n\n"
        "Do the work per the contract you already know from turn 1. "
        "The bead description is canonical."
        f"{close_block}"
    )


def _render_agent_prompt(agent_dir: Path, ctx: dict[str, Any]) -> str:
    """Render the agent's identity prompt (`<agent_dir>/prompt.md`).

    `agent_dir` may be deeply nested (e.g. `pack/agents/builder`). We
    delegate to `render_template` by passing the parent as `agents_dir`
    and the basename as `role`.
    """
    agents_root = agent_dir.parent
    role_name = agent_dir.name
    # Inject the close-contract block if the prompt references it but
    # caller didn't provide one.
    ctx.setdefault("role_step_close_block", _default_close_block(ctx))
    return render_template(agents_root, role_name, **ctx)


def _default_close_block(ctx: dict[str, Any]) -> str:
    """Standard close-contract markdown for the agent's last action.

    Pack-specific guidance (e.g. critic vs lint vs generic) can override
    by passing `role_step_close_block` in ctx.
    """
    bead = ctx.get("role_step_bead_id") or ctx.get("seed_id", "<bead>")
    return (
        "\n\n# REQUIRED FINAL STEP — close your bead\n\n"
        f"Your turn is NOT complete until you close `{bead}`. "
        "Last action of the turn:\n\n"
        f'```bash\nbd close {bead} --reason "<keyword>: <one-line summary>"\n```\n\n'
        "**Stuck or need a human decision?** Don't leave the bead open silently — "
        f'flag it: `bd human {bead} --question="<one-line question>"`. '
        "The orchestrator routes the question to the user and the loop continues. "
        "Leaving the bead open without closing OR flagging means the dispatcher "
        "will nudge you with a follow-up turn, then force-close defensively if "
        "you still don't act.\n"
    )


def _build_nudge_prompt(bead_id: str, verdict_keywords: tuple[str, ...]) -> str:
    """One-shot nudge: prompt the agent to close, escalate, or finish + close."""
    keyword_hint = ""
    if verdict_keywords:
        kws = " | ".join(verdict_keywords)
        keyword_hint = f"\n\nUse one of these keywords in the close reason: `{kws}`."
    return (
        f"Your bead `{bead_id}` is still OPEN — the dispatcher is waiting on it.\n\n"
        "Pick EXACTLY one of these next actions and execute it now:\n\n"
        "1. **Done already?** If your work is complete and you just forgot to "
        f'close, run:\n   `bd close {bead_id} --reason "<keyword>: <one-line>"`'
        f"{keyword_hint}\n\n"
        "2. **Stuck / need a human decision?** Run:\n   "
        f'`bd human {bead_id} --question="<one-line question for the user>"`\n   '
        "The orchestrator will leave the bead open and route the question to the user.\n\n"
        "3. **Still working?** Finish the remaining work, then close the bead "
        "with the same `bd close ...` shape as (1).\n\n"
        "Do not reply with prose only — the dispatcher reads bead state, not your response."
    )


def _persist_session(sess: AgentSession, role: str) -> None:
    """Save sess.session_id back to the RoleSessionStore stashed on sess.

    Best-effort; called from finally blocks so a failure here mustn't
    mask the original exception. Stores the post-turn UUID under `role`
    so the next `agent_step` call for the same (seed, role) resumes
    this Claude conversation.
    """
    try:
        sessions = getattr(sess, "_agent_step_sessions", None)
        if sessions is not None and sess.session_id:
            sessions.set(role, sess.session_id)
    except Exception:  # noqa: BLE001
        pass


__all__ = ["agent_step", "AgentStepResult"]
