"""Pack-shipped `po.commands` callables — thin shells over the `stripe` CLI.

Dispatched via `po stripe-balance` / `po stripe-recent` / `po stripe-mode`.
None of these import the `stripe` SDK in v1; the CLI is the v1 surface
(per pack-convention §"Tool-access preference order"). The `stripe>=9.0`
dep ships in `pyproject.toml` so the SDK is available for agents that
hit a webhook/streaming case the CLI can't express — but this module
stays CLI-only.

`STRIPE_API_KEY` is read by the `stripe` CLI itself from the environment;
we never put it on argv and never echo it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime


_STRIPE_BIN = "stripe"
_TIMEOUT = 10


def _require_cli() -> str:
    path = shutil.which(_STRIPE_BIN)
    if not path:
        print(
            "error: `stripe` CLI not on PATH. "
            "Run `po doctor` for install instructions.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return path


def _run_stripe(args: list[str]) -> dict | list:
    """Run `stripe <args>` and parse JSON stdout. Exits non-zero on failure."""
    _require_cli()
    try:
        proc = subprocess.run(
            [_STRIPE_BIN, *args],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(
            f"error: `stripe {' '.join(args)}` timed out after {_TIMEOUT}s",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        print(f"error: `stripe {' '.join(args)}` failed: {msg}", file=sys.stderr)
        raise SystemExit(2)
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        print(f"error: stripe CLI returned non-JSON: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def balance() -> None:
    """Print Stripe account balance — `stripe balance retrieve`.

    Stripe reports `available` and `pending` as lists of {amount, currency}
    in minor units (cents for USD). We divide by 100 for display.
    """
    data = _run_stripe(["balance", "retrieve"])
    if not isinstance(data, dict):
        print("error: unexpected balance payload (not an object)", file=sys.stderr)
        raise SystemExit(2)

    for bucket in ("available", "pending"):
        rows = data.get(bucket) or []
        for row in rows:
            amt = row.get("amount", 0)
            ccy = (row.get("currency") or "").lower()
            print(f"{bucket:9s}  {amt / 100:12.2f} {ccy}")


def recent_charges(limit: int = 10) -> None:
    """Tabulate recent charges — `stripe charges list --limit <n>`.

    Columns: id | amount | currency | status | created (ISO UTC) | customer.
    """
    if limit < 1 or limit > 100:
        print("error: --limit must be between 1 and 100", file=sys.stderr)
        raise SystemExit(2)

    data = _run_stripe(["charges", "list", "--limit", str(limit)])
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        print("error: unexpected charges payload (no `data` array)", file=sys.stderr)
        raise SystemExit(2)

    header = f"{'id':28s}  {'amount':>10s}  {'ccy':4s}  {'status':10s}  {'created':20s}  customer"
    print(header)
    print("-" * len(header))
    for row in rows:
        rid = str(row.get("id", ""))[:28]
        amt = row.get("amount", 0)
        ccy = (row.get("currency") or "").lower()
        status = str(row.get("status", ""))[:10]
        created_raw = row.get("created")
        created = (
            datetime.fromtimestamp(created_raw, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            if isinstance(created_raw, int | float)
            else ""
        )
        customer = str(row.get("customer") or "")
        print(
            f"{rid:28s}  {amt / 100:>10.2f}  {ccy:4s}  {status:10s}  {created:20s}  {customer}"
        )


def mode() -> None:
    """Inspect `STRIPE_API_KEY` prefix and print test|live|unknown.

    Only the first 8 chars of the key are echoed (e.g. `sk_test_…`); the
    full key never reaches stdout/stderr.
    """
    key = os.environ.get("STRIPE_API_KEY")
    if not key:
        print("mode: unset (STRIPE_API_KEY not in env)")
        return
    redacted = key[:8] + "…"
    if key.startswith("sk_test_"):
        print(f"mode: test  ({redacted})")
    elif key.startswith("sk_live_"):
        print(f"mode: live  ({redacted})")
    else:
        print(f"mode: unknown  ({redacted})")
