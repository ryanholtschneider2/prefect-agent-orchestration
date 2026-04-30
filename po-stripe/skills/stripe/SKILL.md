---
name: stripe
description: Charge customers, issue refunds, inspect balances via Stripe — CLI-first.
---

# Stripe skill — deployment conventions

Use the `stripe` CLI as the primary surface. The Python SDK is a fallback
for webhooks and streaming. Run `po doctor` once before your first call to
verify the binary, env, and live API are all green.

## Canonical vendor docs

- CLI reference: https://docs.stripe.com/stripe-cli
- API reference: https://docs.stripe.com/api
- Vendor llms.txt (AI-friendly docs index): https://docs.stripe.com/llms.txt
- Project-scoped keys (recommended): https://docs.stripe.com/projects

## Deployment rules

1. **Test keys in dev.** `STRIPE_API_KEY` should start with `sk_test_` in
   dev. `po doctor` warns yellow on `sk_live_` unless `PO_ENV=prod`.
2. **Charges over $500 require human approval** — *before* the charge:
   ```
   bd human <issue-id> --question="approve $<amount> charge to <customer> ref <invoice>"
   ```
   Wait for the recorded response. Don't issue the charge until the human
   responds. Skipping this gate is a process violation.
3. **Idempotency keys.** Every write call (`charges create`, `refunds
   create`, `payment_intents create`, …) passes:
   ```
   --idempotency-key "{issue_id}:{step_name}"
   ```
   Same `(issue_id, step)` retried = same outcome. Different step on the
   same issue = different key.
4. **Refunds.** Prefer `stripe refunds create --charge ch_…` over
   re-using a `PaymentIntent`. Cleaner audit trail.

## Quick CLI recipes

```bash
# Inspect balance (no writes)
stripe balance retrieve

# Create a charge (write — must follow rules 2 + 3 above)
stripe charges create \
  --amount 2000 --currency usd \
  --source tok_visa \
  --description "invoice INV-123" \
  --idempotency-key "issue-abc:charge-step"

# List recent charges
stripe charges list --limit 10

# Refund
stripe refunds create --charge ch_3Abcdef \
  --idempotency-key "issue-abc:refund-step"

# Test webhooks locally
stripe listen --forward-to localhost:4242/webhook
```

## Pack-shipped helpers

- `po stripe-balance` — same as `stripe balance retrieve`, parsed and tabulated.
- `po stripe-recent --limit 10` — `stripe charges list` tabulated.
- `po stripe-mode` — prints `test` / `live` / `unknown` based on
  `STRIPE_API_KEY` prefix (never echoes the full key).

## SDK fallback (webhooks / streaming / typed responses)

```python
import os, stripe

stripe.api_key = os.environ["STRIPE_API_KEY"]

intent = stripe.PaymentIntent.create(
    amount=2000,
    currency="usd",
    idempotency_key=f"{issue_id}:{step}",
)
```

The `stripe>=9.0` SDK is a hard dep of this pack; import freely when the
CLI can't express the operation. Keep the same idempotency convention.

## HTTP API

Don't. The CLI and SDK cover everything you'll need.

## Doctor

`po doctor` runs three checks for this pack:

| Check | What it verifies |
|---|---|
| `stripe-cli-installed` | `stripe` binary on PATH; reports `--version` |
| `stripe-env` | `STRIPE_API_KEY` set, well-formed, mode-appropriate |
| `stripe-api` | live `stripe balance retrieve` succeeds within 5s |
