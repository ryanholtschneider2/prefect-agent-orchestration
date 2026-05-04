"""Runtime OAuth token rotation for Claude Code worker processes.

The worker entrypoint can point at a newline-delimited token file via
``PO_CLAUDE_OAUTH_TOKEN_FILE``. Each non-empty, non-comment line is one
token candidate. When a Claude turn hits a terminal rate limit, callers can
advance to the next line, update ``CLAUDE_CODE_OAUTH_TOKEN`` in-process, and
launch a fresh Claude subprocess without restarting the whole worker.
"""

from __future__ import annotations

import os
from pathlib import Path

TOKEN_FILE_ENV = "PO_CLAUDE_OAUTH_TOKEN_FILE"
TOKEN_INDEX_ENV = "PO_CLAUDE_OAUTH_TOKEN_INDEX"
TOKEN_COUNT_ENV = "PO_CLAUDE_OAUTH_TOKEN_COUNT"


def oauth_token_file() -> Path | None:
    """Return the newline-delimited OAuth token file path, if configured."""
    raw = os.environ.get(TOKEN_FILE_ENV)
    if not raw:
        return None
    return Path(raw).expanduser()


def _read_tokens(path: Path) -> list[str]:
    try:
        raw = path.read_text()
    except OSError:
        return []
    tokens: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tokens.append(stripped)
    return tokens


def oauth_token_count() -> int:
    """Return the number of token slots available for failover."""
    raw = os.environ.get(TOKEN_COUNT_ENV)
    try:
        size = int(raw) if raw is not None else 0
    except ValueError:
        return 0
    return size if size > 0 else 0


def oauth_failover_budget() -> int:
    """How many additional token slots are available beyond the current one."""
    count = oauth_token_count()
    return max(0, count - 1)


def rotate_to_next_oauth_pool_slot() -> int | None:
    """Advance ``CLAUDE_CODE_OAUTH_TOKEN`` to the next token-file line.

    Returns the new slot index, or ``None`` when no valid failover target is
    available.
    """
    count = oauth_token_count()
    if count <= 1:
        return None
    raw_idx = os.environ.get(TOKEN_INDEX_ENV)
    try:
        current = int(raw_idx) if raw_idx is not None else -1
    except ValueError:
        return None
    if current < 0 or current >= count:
        return None

    token_file = oauth_token_file()
    if token_file is None:
        return None
    tokens = _read_tokens(token_file)
    if len(tokens) != count:
        return None

    next_index = (current + 1) % count
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = tokens[next_index]
    os.environ[TOKEN_INDEX_ENV] = str(next_index)
    os.environ["PO_AUTH_MODE"] = "oauth"
    os.environ["PO_AUTH_SOURCE"] = "token-file-rotate"
    return next_index
