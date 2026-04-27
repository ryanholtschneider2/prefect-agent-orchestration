# Decision log — prefect-orchestration-5wk.6

- **Decision**: WezTerm backend gated on BOTH `command -v wezterm` AND `$WEZTERM_PANE`.
  **Why**: `wezterm cli split-pane` only attaches to the *current* tab when invoked
  from inside a WezTerm pane. Detecting only the binary would route hosts that
  have wezterm installed but are running in some other terminal to a backend
  that fails. Plan "Risks" section called this out explicitly.
  **Alternatives considered**: detect only via `$WEZTERM_PANE` (rejected — would
  miss `--layout wezterm` forced runs); detect only via the binary (rejected per
  above).

- **Decision**: Pane commands end with `; exec bash` instead of leaving the
  pane to die when the foreground command exits.
  **Why**: For a recording, the final state of `kubectl get pods -w` /
  `watch tree` matters. If the pane closes when the user hits Ctrl-C the
  capture cuts off mid-frame. `exec bash` keeps the pane alive.
  **Alternatives considered**: `read -p "press enter to close"` (uglier on
  camera); rely on `tmux remain-on-exit` (tmux-only, doesn't help WezTerm).

- **Decision**: `--dry-run` prints the pane commands via `printf '%q'` rather
  than re-shell-escaping by hand.
  **Why**: Lets the regression-gate diff the dry-run output against a known
  string without ambiguity, and keeps the smoke test honest about what the
  live path will actually `bash -c`.
  **Alternatives considered**: emit just the pane descriptions (rejected —
  doesn't catch shell-quoting regressions).

- **Decision**: Pane 4 (`po watch`) silently degrades when missing, prints
  `skipping pane 4: po watch unavailable` to stderr.
  **Why**: Plan calls Pane 4 "optional, gated on po-attach landing" — failing
  the script when `po watch` is missing would make the dashboard unusable today.
  **Alternatives considered**: hard-require `po watch` (rejected — too strict);
  silently skip with no log (rejected — debugging would be harder).

- **Decision**: tmux fallback supports both "already inside tmux" and "no tmux
  session" cases, killing+recreating a `snakes-demo` session in the latter.
  **Why**: Recording sessions are usually started from a fresh terminal; the
  in-session split is for users who want to layer the dashboard onto an
  existing workflow. Killing the prior `snakes-demo` session makes re-running
  the script idempotent.
  **Alternatives considered**: only support detached mode (less flexible);
  refuse to overwrite an existing session (worse UX for re-takes).

- **Decision**: No automated unit/e2e tests.
  **Why**: Plan called this out — the script is multiplexer-spawning glue;
  meaningful tests need a live wezterm/tmux/kubectl host. The `--dry-run`
  smoke (run during build + by regression-gate) is the contract.
  **Alternatives considered**: shellcheck / bats tests (deferred — nothing in
  this repo uses them today, would be a new dep).
