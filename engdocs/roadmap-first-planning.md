# Roadmap-first planning

PO should start with the highest useful planning layer, not with immediate
low-level implementation. The workflow is:

1. Align on the goal, roadmap slice, or initiative shape.
2. Prefer a product-level planning workflow when the conversation is still above a single epic.
3. Use epic-level planning once one epic needs deeper shaping.
4. Keep durable artifacts under `.planning/products/` or `.planning/epics/`.
5. Refine that artifact into beads epics and child issues.
6. Choose inline execution, local subagents, or `po` dispatch based on the
   cost of verification and the amount of parallelizable work.

## Preferred workflow

- Product / initiative layer:
  - use `beads-product` to decompose the work into epics, sequencing, and planning waves
- Single-epic layer:
  - use `beads-epic-brainstorm` when one epic still needs collaborative shaping
- Low-level fallback:
  - use `po planning-init` only when you need to create the durable files first and then continue the richer workflow on top

## Artifact layout

`planning-init` is just the scaffold primitive:

```bash
po planning-init --kind=product --slug=<product-slug> --title="Product Name"
po planning-init --kind=epic --slug=<epic-slug> --title="Epic Name"
```

Product mode creates:

- `.planning/products/<slug>/<slug>-vision.md`
- `.planning/products/<slug>/<slug>-epics.md`

Epic mode creates:

- `.planning/epics/<slug>/<slug>-brainstorm.md`
- `.planning/epics/<slug>/<slug>-design.md`
- `.planning/epics/<slug>/<slug>-epic-plan.md`
- `.planning/epics/<slug>/<slug>-issues.md`

These files are intentionally simple. They are durable review artifacts for the
user, future sessions, and delegated workers, not a substitute for the richer
product / epic planning workflows.

## Product vs epic planning

Use product planning when the conversation is still about a broader roadmap
slice, multiple epics, or sequencing across themes. This should usually start
with `beads-product`, not with hand-editing markdown from scratch.

Use epic planning when the work already has a bounded outcome and the remaining
question is how to decompose it into features and implementation beads. This
should usually start with `beads-epic-brainstorm` for complex epics.

## Handoff into beads and PO

After the artifact is refined:

1. File the parent epic and child beads with `bd create`.
2. Wire dependencies in PO's direction.
3. Choose the execution mode:
   - Inline for trivial edits or one-file fixes
   - Local subagents for bounded sidecar work
   - `po run software-dev-full` for one issue that benefits from verifier gates
   - `po run epic` for multi-bead work that should fan out and leave durable run artifacts

The decision boundary stays in the pack skill and docs, not in core orchestration
primitives. That follows the repo's separation rules: concrete workflow guidance
belongs in packs and skills, while core remains a thin execution substrate.
