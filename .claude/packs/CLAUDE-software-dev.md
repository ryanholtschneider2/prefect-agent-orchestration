# po-formulas-software-dev

**What it provides:** Actor-critic multi-agent pipelines for autonomous software development — full and fast variants, epic fan-out, graph dispatch, and skill evals.

**When to use:**
- Dispatching a beads issue for autonomous implementation (new features, bug fixes, refactors)
- Running a DAG-ordered epic of child issues in parallel
- Evaluating a pack's skills with LLM-judged rubrics

**Key verbs:** `software-dev-full`, `software-dev-fast`, `software-dev-edit`, `epic`, `graph`, `skill-evals`, `epic-finalize`
- `software-dev-edit`: ultra-thin plan → build → lint → close; for trivial single-file edits and doc tweaks; pair with `epic-finalize` as the last epic child.

**Key paths:** `po_formulas/agents/<role>/prompt.md`, `po_formulas/software_dev.py`, `po_formulas/epic.py`

**Skip if:** The task doesn't involve code changes, or you only need scheduling / orchestration utilities without an actor-critic loop.

**Read more:** `po show software-dev-full`, `po show epic`, `engdocs/formula-modes.md`
