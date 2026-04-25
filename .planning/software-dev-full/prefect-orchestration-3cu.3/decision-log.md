# Decision log — prefect-orchestration-3cu.3 (po-slack tool pack)

## Build iter 1

- **Decision**: Pack lives in a brand-new sibling repo at
  `/home/ryan-24/Desktop/Code/personal/nanocorps/po-slack/`, not inside
  the `prefect-orchestration` rig.
  **Why**: Plan + issue `pw4` rule — pack-contrib code lands in its own
  repo so core stays slim and `po install --editable <path>` is the
  single dev-loop knob. Mirrors the `software-dev/po-formulas/` layout.
  **Alternatives considered**: top-level package inside this repo
  (would couple lifecycle to core), nested under `nanocorps/`
  collection repo (no such repo exists).

- **Decision**: `slack_sdk` is the only runtime dep; the official
  Slack CLI is documented in the skill but never shelled out to.
  **Why**: Plan §"Slack client" — the Slack CLI is a Deno-based
  automation-app builder, not a "send a message" tool. Triage
  recommended this; the CLI link still satisfies AC#2 ("CLI-first
  with links").
  **Alternatives considered**: subprocess `slack` for posts (heavy
  Deno dep on the host, no real upside).

- **Decision**: Overlay file lands at
  `overlay/nanocorp-rules/slack.md` (rig-root nested), not at
  `overlay/AGENTS.md` and not under `overlay/.claude/`.
  **Why**: (1) The rig already has an `AGENTS.md`; placing the rules
  there would silently no-op via `_copy_tree`'s skip-existing
  semantics. (2) Writing to `.claude/` paths is gated by Claude
  Code's sensitive-file policy. A non-`.claude` nested dir is
  discoverable, never collides, and verified by `materialize_packs`
  in a tempdir to land at `<rig>/nanocorp-rules/slack.md`.
  **Alternatives considered**: `overlay/AGENTS.md` (collision risk),
  `overlay/.claude/nanocorp-rules/slack.md` (sensitive-path block).

- **Decision**: Doctor check `slack-bot-token` returns **yellow** for
  unset env, never red.
  **Why**: The pack is opt-in. A rig that simply doesn't use Slack
  shouldn't have a red `po doctor` row just because it installed the
  pack. Red is reserved for "set but malformed" (bad prefix) — that's
  a real misconfiguration.
  **Alternatives considered**: red on missing (too noisy), green
  with a note (hides the fact it's unconfigured).

- **Decision**: `slack-workspace-reach` uses a 3-second client timeout
  and returns yellow on `socket.timeout` / `OSError` / unknown
  Slack errors (e.g. `ratelimited`); only `invalid_auth` /
  `not_authed` / `account_inactive` are red.
  **Why**: Stays within core's 5-second pack-check budget; doesn't
  page the operator on a flaky network — only on genuine auth
  failures.
  **Alternatives considered**: longer timeout (would risk core's
  pack-check killing the row to yellow anyway).

- **Decision**: `slack-react` treats `already_reacted` as success
  and prints `ok already_reacted`.
  **Why**: Reactions are naturally idempotent from the caller's
  perspective; surfacing it as a non-zero exit would force every
  caller script to wrap in conditional logic.
  **Alternatives considered**: hard-fail (over-strict), silent
  success (loses the signal that the reaction was already there).

- **Decision**: Cross-repo wiring is verified by a new test file
  `tests/e2e/test_po_slack_pack_install.py` in the rig that uses
  `pytest.importorskip("po_slack")`.
  **Why**: Mirrors the existing `test_po_gmail_pack_install.py`
  precedent — tests the entry-point wiring without coupling rig CI
  to the sibling pack being checked out. Skip cleanly when missing.
  **Alternatives considered**: tests live only in po-slack's repo
  (would miss the wiring contract on the core side); shell out
  `po install` from the test (would mutate user state).

- **Decision**: Pack ships its own `tests/test_commands.py` +
  `tests/test_checks.py` with a `FakeWebClient` monkeypatch — no
  network, no Slack credentials needed.
  **Why**: Hermetic CI; covers all eight branch combinations called
  out in the plan's test plan. Verified locally: 19 passed in 0.15s.
  **Alternatives considered**: hit Slack with a real token (flaky,
  requires CI secrets, against repo principle of hermetic tests).
