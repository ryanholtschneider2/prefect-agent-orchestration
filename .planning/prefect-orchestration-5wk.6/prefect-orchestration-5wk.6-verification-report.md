# Verification Report: prefect-orchestration-5wk.6

## Provenance

Implementation produced by `po run software-dev-full` (flow-run
`8abc3efb-390a-4b36-b6c5-98a9b6d9f792`). The flow completed
triage → plan → build → lint → unit-iter-1 → e2e-iter-1 →
regression-iter-1 (no regression) and then died at the `review`
step. The two files it produced
(`scripts/snakes-demo/dashboard.sh`, `engdocs/snakes-demo.md`)
are the only artifacts relevant to this issue and were verified
manually after the failure.

Pre-existing unit/e2e baseline failures (6 unit, 3 e2e) are
unrelated to this issue's scope and were marked `regression: false`
by the regression gate.

## Acceptance Criteria

| # | Criterion | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Script works in WezTerm | `--dry-run --layout wezterm` | PASS | 4 panes printed (incl. Pane 4 with `--epic-id epic-x`) |
| 2 | Script works in tmux fallback | Live tmux smoke (inside existing tmux session) | PASS | `tmux list-panes -t wrapper:0` showed 3 panes after invocation; layout `tiled` |
| 3 | engdocs/snakes-demo.md walkthrough covers provision rig → seed beads → deploy chart → dashboard.sh → dispatch epic → record | Read doc end-to-end | PASS | All six sections present; both OBS and asciinema recording paths documented |

## Smoke Tests

```
$ bash -n scripts/snakes-demo/dashboard.sh   # syntax OK
$ bash scripts/snakes-demo/dashboard.sh       # exit=2 (missing --rig-path), prints usage
$ bash scripts/snakes-demo/dashboard.sh --rig-path /tmp/x --dry-run --layout tmux
backend=tmux ... pane1/2/3 + finalize printed, no Pane 4 (no --epic-id)
$ bash scripts/snakes-demo/dashboard.sh --rig-path /tmp/x --epic-id epic-x --dry-run --layout wezterm
backend=wezterm ... pane1/2/3 + pane4 printed, URL includes ?tag=epic:epic-x
```

Live tmux invocation from inside an existing tmux session created
3 panes in tiled layout (Pane 4 not exercised since `--epic-id`
omitted). WezTerm live path not exercised (current session is
plain tmux, not wezterm); covered by dry-run only.

## Regression Check

PO's `regression-iter-1.json` verdict: `regression_detected: false`.
Pre-existing failures (unit: tmux argv, cli core_verbs, deployments
po-list, mail_prompt.md missing; e2e: test_po_deploy_cli, two
test_po_run_from_file) are unrelated to this issue.

## Confidence

**HIGH** for tmux + dry-run paths and walkthrough completeness.
**MEDIUM** for live WezTerm spawn (verified via dry-run only — no
WezTerm session available to exercise the live `wezterm cli
split-pane` calls; the script gates that path on `$WEZTERM_PANE`
being set, so the failure mode in the wrong env is a clean error).
