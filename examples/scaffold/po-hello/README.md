# po-hello

A [po](https://github.com/) pack scaffolded with `po new pack`.

## Install (editable, for development)

```bash
po packs install --editable .
po list            # shows `po-hello-ping` (and any formulas you add)
po po-hello-ping      # -> "po-hello: pong"
```

## Add to it

```bash
po new formula my-flow --pack .     # adds a @flow under po.formulas
po new skill my-skill --pack .      # adds skills/my-skill/SKILL.md + evals/
po new agent my-agent --pack .      # adds an agent prompt + cron formula + evals
```

## Layout

```
po-hello/
  pyproject.toml          # [project.entry-points."po.*"] groups
  po_hello/
    __init__.py
    commands.py           # po.commands utility ops
  overlay/
    CLAUDE-po-hello.md        # ~150-word discovery summary copied into rigs
  skills/                 # SKILL.md + evals/ (po new skill)
```
