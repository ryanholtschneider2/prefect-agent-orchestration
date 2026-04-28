# minimal-task formula

Lightweight PO formula shipped by `po-formulas-software-dev`. Pipeline:

```
triage → plan → build → lint → close
```

Single-pass plan, single build iteration (with one retry on lint
failure), no critic / verifier / ralph / docs / demo / learn. Designed
for **fanout demos** where running the full actor-critic loop on every
trivial child of an epic would burn tokens with no signal — the
canonical case is the snake-bead 100-way demo (see
`engdocs/snakes-demo.md`).

## Fail-out semantics

Lint runs after each build. If lint fails, the flow does ONE more
build iteration with the prior lint summary fed in as a
`revision_note`, then lints again. Two failures in a row → flow
raises `RuntimeError`, the bead stays `in_progress`, and run-dir
artifacts at `<rig>/.planning/minimal-task/<issue>/` remain for
`po artifacts` / `po logs`. **No ralph fallback** — recovery isn't
worth the spend at fanout scale.

## Lint verdict file (additive prompt change)

The shipped linter prompt now writes
`verdicts/lint-iter-<N>.json` with `{"verdict": "pass"|"fail",
"summary": "..."}` in addition to the existing `lint-iter-<N>.log`.
`minimal-task` reads this file via
`prefect_orchestration.parsing.read_verdict` to gate the loop —
agents writing verdicts to files instead of orchestrators parsing
prose is the principle (`principles.md` §"derived mechanisms" /
`$RUN_DIR/verdicts/<step>.json`).

`software-dev-full` ignores the new file; the change is backwards-
compatible.

## See also

- Pack README: `software-dev/po-formulas/README.md` — usage examples
  and CLI invocation.
- `principles.md` §1, §4 — formulas vs commands; new flows go through
  `po run`.
- `pack-convention.md` — entry-point + prompt layout (this formula
  reuses the existing `triager` / `builder` / `linter` agent prompts;
  no new prompt files added).
