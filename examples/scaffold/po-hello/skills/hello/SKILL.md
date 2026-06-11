---
name: hello
description: <1-2 sentence summary of when an agent should load this skill>.
---

# hello skill

Replace this with the canonical how-to for hello. Keep it operational:
the rules that matter, the canonical commands, the footguns.

## Rules that matter

1. <rule one>
2. <rule two>

## Canonical recipes

```bash
# show the real commands here
```

## Evals

This skill ships an `evals/` suite (`cases.yaml` + `rubrics.yaml`). Run it with:

```bash
po run skill-evals --pack po-hello --skill hello --dry-run   # CI-safe smoke
po run skill-evals --pack po-hello --skill hello             # real judge
```
