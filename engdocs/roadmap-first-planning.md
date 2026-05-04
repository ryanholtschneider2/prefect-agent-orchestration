# Roadmap-first planning

PO should start with the highest useful planning layer, not with immediate
low-level implementation. The workflow is:

1. Align on the goal, roadmap slice, or epic shape.
2. Create a durable planning artifact under `.planning/products/` or
   `.planning/epics/`.
3. Refine that artifact into beads epics and child issues.
4. Choose inline execution, local subagents, or `po` dispatch based on the
   cost of verification and the amount of parallelizable work.

## Artifact layout

Use the pack-shipped scaffold command:

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
user, future sessions, and delegated workers, not a second orchestration system.

## Product vs epic planning

Use product planning when the conversation is still about a broader roadmap
slice, multiple epics, or sequencing across themes.

Use epic planning when the work already has a bounded outcome and the remaining
question is how to decompose it into features and implementation beads.

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
