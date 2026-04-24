# Verification Report — prefect-orchestration-shj (iter 2)

| AC | Evidence | Verdict |
|---|---|---|
| 1. `po.deployments` entry-point group | `prefect_orchestration/deployments.py:40` reads `entry_points(group="po.deployments")`. Live discovery resolves `software-dev → po_formulas.deployments:register` (smoke RE-SMOKE block). Unit tests 16/16 pass. | PASS |
| 2. Example `po-formulas` pack: nightly `epic-sr-8yu` at 09:00 | Confirmed at `/home/ryan-24/Desktop/Code/personal/nanocorps/software-dev/po-formulas/po_formulas/deployments.py` — `register()` returns `epic_run.to_deployment(name="epic-sr-8yu-nightly", schedule=Cron("0 9 * * *", timezone="America/New_York"), parameters={"epic_id": "sr-8yu"})`. Its `pyproject.toml` declares `[project.entry-points."po.deployments"] software-dev = "po_formulas.deployments:register"`. Sibling pack is editable-installed in the core venv; `po deploy` row renders live. | PASS |
| 3. `po deploy` lists; `--apply` creates on server | Live `po deploy` renders the pack/deployment/flow/schedule/params row. `po deploy --apply` without `PREFECT_API_URL` exits 2 with guardrail message. 5/5 e2e tests pass (list, apply w/ + w/o env, error surfacing, empty-pack). | PASS |
| 4. README documents `register()` convention | `README.md` §Deployments covers entry-point declaration, `register()` return contract, `--apply` semantics, automations note. | PASS |
| 5. `po run` unchanged | `po --help` still shows `list/show/run/deploy`; regression suite `tests/test_agent_session_tmux.py` 17/17 pass; no diff in `run` code path. | PASS |

**Regressions:** none — 56/56 full-repo tests pass; no baseline suite newly fails.

**Mocks in prod:** none. Unit tests stub only the internal `_iter_entry_points` seam.

**Verdict: APPROVED.** AC2 gap from iter-1 closed by placing the example deployment in the sibling `po-formulas` pack (the correct home — this repo's `po_formulas/` module predates the split and is being retired in favor of the external pack, which is editable-installed for development).
