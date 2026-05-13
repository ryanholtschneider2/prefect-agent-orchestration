# Deprecating a `po.formulas` pack (from dgr)

Lessons from stubbing `po-formulas-prompt` after its functionality
moved into core's `agent-step`. Apply when retiring (or about to
register) any `po.formulas` entry-point.

- **EP load-order is last-write-wins, silently.** `cli._load_formulas`
  iterates `entry_points(group='po.formulas')` and assigns into a
  dict; whichever distribution loads last wins. There is no collision
  warning. If you deprecate a pack-level formula but core (or another
  pack) still ships the *same EP name*, `po run <name>` resolves to
  the **other** registration — your deprecation stub never fires for
  the canonical CLI path. When deprecating, also stub (or rename) every
  parallel registration of that name; `po packs list` shows where each
  formula is registered.
- **`bd create` accepts `--metadata '<json>'`, NOT `--set-metadata
  <k>=<v>`.** `--set-metadata` is `bd update`-only. Migration recipes
  that need to bake `po.agent` (or any metadata) at creation time must
  use the JSON form:
  `bd create … --metadata '{"po.agent":"general"}' --json | jq -r .id`.
  The `bd create -q` form prints `✓ Created issue: <id> — <title>` —
  `awk '{print $NF}'` extracts the title, not the id; prefer
  `--json | jq -r .id` (or the `python3 -c 'import sys, json;
  print(json.load(sys.stdin)["id"])'` jq-free fallback — `python3`,
  not `python`, since modern Linux distros don't symlink the latter).
- **Hatchling wheel discovery is `.py`-centric.** Packs that ship
  `po_formulas_<name>/agents/<role>/prompt.md` must add
  `[tool.hatch.build.targets.wheel.force-include]` mapping
  `"po_formulas_<name>/agents" = "po_formulas_<name>/agents"` so the
  prompt files survive a wheel install. Without it,
  `discover_agent_dir(<role>)` resolves at dev time (sdist / editable)
  but raises after `uv tool install` from the built wheel.
- **"No legacy imports" tests must check the module namespace, not
  source text.** Use `dir(module)` / `hasattr(module, name)` rather
  than `grep`-ing the file: deprecation docstrings legitimately
  mention retired symbols (e.g. `AgentSession`) by name when
  explaining what moved where, and a source-text assertion will trip
  on the docstring itself.
