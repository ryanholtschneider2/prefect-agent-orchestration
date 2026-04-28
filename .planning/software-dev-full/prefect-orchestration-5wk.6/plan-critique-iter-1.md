# Critique — plan-iter-1

**Verdict: approved**

Fit: covers both ACs verbatim — script + walkthrough doc. Backend choice
(WezTerm requires `WEZTERM_PANE`, tmux fallback) is the right
detection rule and a well-known footgun caught up front.

Scope: appropriately small. `--dry-run` as the regression-gate hook is
the right call — actually spawning multiplexers in CI is brittle, and
the issue explicitly notes this. Pane 4 gated on `po watch` availability
is good defensive behavior given po-attach hasn't landed.

Approach: grounded — references the actual rig layout (`$RIG_PATH/snakes/`),
the existing namespace convention (`po`), and matches the issue's pane
spec line-for-line.

AC testability: AC1 mechanically verified by 4 dry-run invocations
asserting backend selection + expected commands. AC2 verified by
section-heading grep. Both are runnable from regression-gate.

Nits (non-blocking):

- The `xdg-open … || printf` fallback in pane 1 will return early if
  `xdg-open` succeeds, leaving the pane with no `exec bash` trailer —
  worth confirming the pane stays alive (the planned `; exec bash`
  trailer covers this, just be sure it's appended after the
  `||` branch, e.g. wrap in `( … ) ; exec bash`).
- Consider mentioning that `tmux select-layout tiled` after a 4-pane
  split can produce odd aspect ratios for recording — `main-vertical`
  may read better on a 16:9 capture. Not a blocker; recordist preference.
