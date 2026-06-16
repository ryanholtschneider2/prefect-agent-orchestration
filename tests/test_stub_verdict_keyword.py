from __future__ import annotations

from prefect_orchestration.agent_session import stub_verdict_keyword


def _rendered(kw_list: str) -> str:
    # Mirrors agent_step's bold-markdown rendering of the keyword line.
    return f"**Required verdict keyword (case-insensitive):** {kw_list}.\nUse EXACTLY one.\n"


def test_parses_bold_keyword_line_pass_fail():
    # The agentic reviewer: affirmative is the first keyword.
    assert stub_verdict_keyword(_rendered("`pass` | `fail`")) == "pass"


def test_prefers_affirmative_over_order():
    assert stub_verdict_keyword(_rendered("`rejected` | `approved`")) == "approved"


def test_clean_and_merged_fall_back_to_first():
    assert stub_verdict_keyword(_rendered("`clean` | `failed`")) == "clean"
    assert stub_verdict_keyword(_rendered("`merged` | `failed`")) == "merged"


def test_no_keyword_line_defaults_complete():
    assert stub_verdict_keyword("just some prompt with no verdict line") == "complete"
