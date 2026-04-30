# Stripe (po-stripe pack)

If you're touching Stripe in this rig, read the full skill at
`.claude/skills/po-stripe/stripe/SKILL.md` before your first call.

The three rules that matter:

1. **Test keys in dev.** `STRIPE_API_KEY` starts with `sk_test_`. `po doctor`
   warns on `sk_live_` outside prod.
2. **Charges > $500 need a human.** Run `bd human <issue> --question="approve
   $<amt> charge to <customer>"` and wait for the recorded response *before*
   the `stripe charges create` call.
3. **Idempotency keys.** Every write call passes
   `--idempotency-key "{issue_id}:{step_name}"`.

Run `po doctor` before any first Stripe call — confirms CLI, env, and live
reachability in one pass. Use `po stripe-mode` to verify which key mode
you're in without echoing the full key.

CLI > SDK > HTTP. Use `stripe ...` shell calls by default; reach for the
`stripe` Python SDK only for webhooks/streaming.
