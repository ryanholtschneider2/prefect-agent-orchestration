"""Pack-contributed `po.doctor_checks`.

Each callable is registered in `pyproject.toml` and returns a
`prefect_orchestration.doctor.DoctorCheck`. Core wraps each call in a 5s
timeout; on timeout the row is yellow.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from prefect_orchestration.doctor import DoctorCheck


_BIN = "stripe"
_BREW_HINT = (
    "macOS: `brew install stripe/stripe-cli/stripe` · "
    "Linux: see https://docs.stripe.com/stripe-cli (apt repo or tarball)"
)


def cli_installed() -> DoctorCheck:
    """Verify the `stripe` CLI is on PATH and runnable."""
    name = "stripe CLI present"
    path = shutil.which(_BIN)
    if not path:
        return DoctorCheck(
            name=name, status="red", message="`stripe` not on PATH", hint=_BREW_HINT
        )
    try:
        proc = subprocess.run(
            [_BIN, "--version"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return DoctorCheck(
            name=name,
            status="yellow",
            message="`stripe --version` timed out",
            hint="check your stripe CLI install for a hang",
        )
    except OSError as exc:
        return DoctorCheck(
            name=name,
            status="red",
            message=f"failed to invoke stripe: {exc}",
            hint=_BREW_HINT,
        )
    if proc.returncode != 0:
        return DoctorCheck(
            name=name,
            status="red",
            message=f"`stripe --version` exited {proc.returncode}",
            hint=_BREW_HINT,
        )
    return DoctorCheck(
        name=name, status="green", message=(proc.stdout or "").strip() or path
    )


def env_set() -> DoctorCheck:
    """Verify `STRIPE_API_KEY` is set and well-formed.

    Mode hygiene: in dev (default), `sk_live_` is yellow; in prod
    (`PO_ENV=prod`), `sk_test_` is yellow. Only the first 8 chars of the
    key are ever included in `message`.
    """
    name = "STRIPE_API_KEY"
    key = os.environ.get("STRIPE_API_KEY")
    if not key:
        return DoctorCheck(
            name=name,
            status="red",
            message="STRIPE_API_KEY unset",
            hint="export STRIPE_API_KEY from your vault / .env",
        )
    if not key.startswith(("sk_test_", "sk_live_")):
        return DoctorCheck(
            name=name,
            status="red",
            message="STRIPE_API_KEY malformed",
            hint="should start with sk_test_ or sk_live_",
        )

    redacted = key[:8] + "…"
    is_prod = os.environ.get("PO_ENV", "").lower() == "prod"
    if key.startswith("sk_live_"):
        if is_prod:
            return DoctorCheck(
                name=name, status="green", message=f"live key set ({redacted})"
            )
        return DoctorCheck(
            name=name,
            status="yellow",
            message=f"live key in non-prod env ({redacted})",
            hint="use sk_test_ in dev, or set PO_ENV=prod to silence",
        )
    # sk_test_
    if is_prod:
        return DoctorCheck(
            name=name,
            status="yellow",
            message=f"test key in prod env ({redacted})",
            hint="use sk_live_ in prod, or unset PO_ENV",
        )
    return DoctorCheck(name=name, status="green", message=f"test key set ({redacted})")


def api_reachable() -> DoctorCheck:
    """Ping Stripe via `stripe balance retrieve`.

    Short-circuits to yellow when env or CLI is missing — never blocks.
    Subprocess capped at 5s.
    """
    name = "stripe API reachable"
    if not os.environ.get("STRIPE_API_KEY"):
        return DoctorCheck(
            name=name,
            status="yellow",
            message="STRIPE_API_KEY unset — skipping live ping",
            hint="set STRIPE_API_KEY (see stripe-env)",
        )
    if not shutil.which(_BIN):
        return DoctorCheck(
            name=name,
            status="yellow",
            message="`stripe` not on PATH — skipping live ping",
            hint="install stripe CLI (see stripe-cli-installed)",
        )
    try:
        proc = subprocess.run(
            [_BIN, "balance", "retrieve"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return DoctorCheck(
            name=name,
            status="yellow",
            message="`stripe balance retrieve` timed out",
            hint="check network connectivity / re-verify STRIPE_API_KEY",
        )
    except OSError as exc:
        return DoctorCheck(
            name=name,
            status="red",
            message=f"failed to invoke stripe: {exc}",
            hint="reinstall the stripe CLI",
        )
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()[:1]
        snippet = msg[0] if msg else f"exit {proc.returncode}"
        return DoctorCheck(
            name=name,
            status="yellow",
            message=f"stripe balance retrieve failed: {snippet}",
            hint="re-verify STRIPE_API_KEY (see stripe-env)",
        )
    return DoctorCheck(name=name, status="green", message="stripe balance retrieve OK")
