"""Complexity-based model selection at dispatch.

The dispatching agent sizes a task's *complexity* (a judgment) and this
module maps that judgment to a concrete model (deterministic transport):

    routine / moderate work   -> sonnet   (the workhorse)
    hard / architectural work -> opus     (the heavy lifter)

Haiku is deliberately never selected — it is too weak for the agentic
software-dev loop (see prefect-orchestration-3olt). Spreading routine work
onto Sonnet keeps Opus rate-limit headroom for the work that actually needs it.

Two entry points:

- `select_model(complexity, provider=...)` — the helper a flow calls to
  auto-select (`PO_FORMULA_MODE` graph, a per-role default, etc.).
- the `po run --complexity <tier>` CLI flag — the convention a dispatching
  agent uses: it resolves to `--model <model>` when `--model` is not given.

Provider equivalents let the same convention extend past Claude (codex →
gpt-5-codex). Today codex exposes a single coding model, so both tiers map
to it; the table is where a cheaper-tier codex model would slot in later.
"""

from __future__ import annotations

__all__ = [
    "DEFAULT_PROVIDER",
    "PROVIDER_MODELS",
    "complexity_tiers",
    "normalize_complexity",
    "provider_from_backend",
    "select_model",
]

DEFAULT_PROVIDER = "claude"

# tier -> model alias, per provider. Two tiers only: "routine" and "hard".
# Haiku never appears here by design.
PROVIDER_MODELS: dict[str, dict[str, str]] = {
    "claude": {"routine": "sonnet", "hard": "opus"},
    # codex exposes a single coding model today; both tiers map to it.
    "codex": {"routine": "gpt-5-codex", "hard": "gpt-5-codex"},
}

# Natural words a dispatching agent might use -> canonical tier.
_COMPLEXITY_ALIASES: dict[str, str] = {
    # routine / Sonnet
    "trivial": "routine",
    "simple": "routine",
    "easy": "routine",
    "low": "routine",
    "small": "routine",
    "routine": "routine",
    "moderate": "routine",
    "medium": "routine",
    "standard": "routine",
    # hard / Opus
    "hard": "hard",
    "high": "hard",
    "complex": "hard",
    "difficult": "hard",
    "architectural": "hard",
    "architecture": "hard",
    "design": "hard",
    "tricky": "hard",
}


def complexity_tiers() -> tuple[str, ...]:
    """The accepted complexity words (canonical tiers + aliases), sorted."""
    return tuple(sorted(_COMPLEXITY_ALIASES))


def normalize_complexity(complexity: str) -> str:
    """Resolve a free-form complexity word to its canonical tier.

    Case- and whitespace-insensitive. Raises `ValueError` (listing the
    accepted words) on an unrecognized input so a typo at dispatch fails
    loudly instead of silently picking the wrong model.
    """
    key = str(complexity).strip().lower()
    tier = _COMPLEXITY_ALIASES.get(key)
    if tier is None:
        accepted = ", ".join(complexity_tiers())
        raise ValueError(f"unknown complexity {complexity!r}; accepted: {accepted}")
    return tier


def provider_from_backend(backend: str | None) -> str:
    """Map a `PO_BACKEND` value to a model provider.

    `codex-*` backends -> "codex"; everything else (cli/tmux/stub/None) ->
    the default "claude".
    """
    if backend and backend.strip().lower().startswith("codex"):
        return "codex"
    return DEFAULT_PROVIDER


def select_model(complexity: str, *, provider: str = DEFAULT_PROVIDER) -> str:
    """Return the model alias for a task `complexity` and `provider`.

    >>> select_model("hard")
    'opus'
    >>> select_model("routine")
    'sonnet'
    >>> select_model("architectural", provider="codex")
    'gpt-5-codex'

    Raises `ValueError` on an unknown complexity word or unknown provider.
    """
    tier = normalize_complexity(complexity)
    prov = str(provider).strip().lower()
    table = PROVIDER_MODELS.get(prov)
    if table is None:
        accepted = ", ".join(sorted(PROVIDER_MODELS))
        raise ValueError(f"unknown provider {provider!r}; accepted: {accepted}")
    return table[tier]
