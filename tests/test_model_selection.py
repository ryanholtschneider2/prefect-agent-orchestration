"""Complexity -> model mapping (prefect-orchestration-3olt)."""

from __future__ import annotations

import pytest

from prefect_orchestration.model_selection import (
    PROVIDER_MODELS,
    complexity_tiers,
    normalize_complexity,
    provider_from_backend,
    select_model,
)


@pytest.mark.parametrize(
    "word, model",
    [
        ("routine", "sonnet"),
        ("moderate", "sonnet"),
        ("medium", "sonnet"),
        ("trivial", "sonnet"),
        ("simple", "sonnet"),
        ("low", "sonnet"),
        ("hard", "opus"),
        ("architectural", "opus"),
        ("complex", "opus"),
        ("high", "opus"),
        ("difficult", "opus"),
    ],
)
def test_select_model_claude_tiers(word: str, model: str) -> None:
    assert select_model(word) == model


def test_select_model_is_case_and_space_insensitive() -> None:
    assert select_model("  HARD ") == "opus"
    assert select_model("Architectural") == "opus"


def test_never_selects_haiku() -> None:
    # The whole point of 3olt: routine work goes to Sonnet, hard to Opus,
    # haiku is too weak and must never be the selection for any tier/provider.
    selected = {model for table in PROVIDER_MODELS.values() for model in table.values()}
    assert "haiku" not in selected


def test_provider_equivalents_codex() -> None:
    assert select_model("routine", provider="codex") == "gpt-5-codex"
    assert select_model("hard", provider="codex") == "gpt-5-codex"


def test_unknown_complexity_raises_with_accepted_list() -> None:
    with pytest.raises(ValueError, match="unknown complexity"):
        select_model("galaxy-brained")


def test_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="unknown provider"):
        select_model("hard", provider="gemini")


def test_normalize_complexity_returns_canonical_tier() -> None:
    assert normalize_complexity("trivial") == "routine"
    assert normalize_complexity("architecture") == "hard"


def test_complexity_tiers_are_sorted_and_nonempty() -> None:
    tiers = complexity_tiers()
    assert tiers
    assert list(tiers) == sorted(tiers)
    assert "hard" in tiers and "routine" in tiers


@pytest.mark.parametrize(
    "backend, provider",
    [
        (None, "claude"),
        ("", "claude"),
        ("cli", "claude"),
        ("tmux", "claude"),
        ("stub", "claude"),
        ("codex-cli", "codex"),
        ("codex-tmux", "codex"),
        ("CODEX-TMUX-STREAM", "codex"),
    ],
)
def test_provider_from_backend(backend: str | None, provider: str) -> None:
    assert provider_from_backend(backend) == provider


def test_root_export() -> None:
    import prefect_orchestration

    assert prefect_orchestration.select_model("hard") == "opus"
