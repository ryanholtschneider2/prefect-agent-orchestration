"""Explicit capacity retry/fallback policy materialization for agent runtimes."""

from __future__ import annotations

from typing import Any

from prefect_orchestration.agent_session import RuntimeFallback
from prefect_orchestration.backend_select import (
    adapt_backend_to_start_command,
    select_default_backend,
)
from prefect_orchestration.role_config import resolve_capacity_policy


def instantiate_backend(
    backend_factory: Any,
    *,
    seed_id: str,
    role: str,
    start_command: str | None = None,
    tmux_scope: str | None = None,
) -> Any:
    """Construct CLI or tmux backends across their intentionally varied shapes."""
    backend_kwargs = {"start_command": start_command} if start_command else {}
    if tmux_scope is not None:
        try:
            return backend_factory(
                issue=seed_id, role=role, scope=tmux_scope, **backend_kwargs
            )
        except TypeError:
            pass
    try:
        return backend_factory(issue=seed_id, role=role, **backend_kwargs)
    except TypeError:
        try:
            return backend_factory(**backend_kwargs)
        except TypeError:
            return backend_factory()


def materialize_capacity_policy(
    *, seed_id: str, role: str, tmux_scope: str | None = None
) -> tuple[int, tuple[RuntimeFallback, ...]]:
    """Resolve validated env transport into concrete ordered backend runtimes."""
    policy = resolve_capacity_policy()
    fallbacks: list[RuntimeFallback] = []
    for spec in policy.fallbacks:
        backend_factory = select_default_backend(override=spec.backend)
        backend_factory = adapt_backend_to_start_command(
            backend_factory, spec.start_command
        )
        backend = instantiate_backend(
            backend_factory,
            seed_id=seed_id,
            role=role,
            start_command=spec.start_command,
            tmux_scope=tmux_scope,
        )
        fallbacks.append(
            RuntimeFallback(
                backend=backend,
                model=spec.model,
                effort=spec.effort,
                label=spec.label,
                account=spec.account,
                account_class=spec.account_class,
            )
        )
    return policy.retries, tuple(fallbacks)


__all__ = ["instantiate_backend", "materialize_capacity_policy"]
