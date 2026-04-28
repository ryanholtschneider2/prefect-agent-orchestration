# Baseline notes — prefect-orchestration-5wk.6

Baseline captured at 2026-04-27T15:37:56-04:00 (unit-only; e2e + playwright skipped per layer-aware command).

## Pre-existing failures: 30 failed, 442 passed, 2 skipped (28.65s)

These failures exist BEFORE any changes for 5wk.6. Do not attribute them to this issue.

Failing test files:
- `tests/test_agent_session_mail.py` (8 failures — TypeError in fixtures)
- `tests/test_agent_session_overlay.py` (4 failures — TypeError)
- `tests/test_agent_session_tmux.py` (6 failures — argv/session derivation)
- `tests/test_cli_packs.py` (7 failures — Typer Usage / refusal assertions)
- `tests/test_deployments.py::test_po_list_still_works` (1)
- `tests/test_mail.py::test_prompt_fragment_exists_and_mentions_inbox` (1)
- `tests/test_parsing.py` (2 — prompt_for_verdict)

Don't make it worse: regression-gate compares post-change failures against this set.
