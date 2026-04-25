# Decision log — prefect-orchestration-3cu.1 (po-gmail tool pack)

- **Decision**: Pack lives at `/home/ryan-24/Desktop/Code/personal/nanocorps/po-gmail/`,
  a sibling of the rig — not inside `prefect-orchestration/`.
  **Why**: principle §pw4 (pack-contrib code in its own repo) and the
  triage's "likely needs to be created at `../po-gmail/`".
  **Alternatives considered**: a subdir under `prefect-orchestration/`
  (would couple core's git history to pack churn — rejected); a subdir
  under `software-dev/` (that namespace is for the formula pack — rejected).

- **Decision**: Mail agent identity is `WildForge`, not the requested
  `prefect-orchestration-3cu.1-builder`.
  **Why**: the mcp-agent-mail server enforces adjective+noun naming
  and silently auto-generated `WildForge` when my requested name was
  rejected.
  **Alternatives considered**: re-register with a manually picked
  adjective+noun (no upside; auto-generated name is already persisted
  and the reservation succeeded under it).

- **Decision**: `gmail-send` defaults `dry_run=True` and reads body
  from `sys.stdin`.
  **Why**: plan §4 + triage "Send safety". Autonomous agents must not
  silently emit mail; dry-run-by-default keeps the AC ("stdin body")
  workable while making the destructive path explicit.
  **Alternatives considered**: `--confirm` interactive prompt (no TTY
  in CI / Prefect workers — rejected); separate `gmail-send-dryrun`
  command (doubles surface area for the same op — rejected).

- **Decision**: `_run_with_timeout` uses a `threading.Thread(daemon=True)`
  with `.join(timeout)` rather than `signal.SIGALRM` or asyncio.
  **Why**: SIGALRM only works on the main thread; checks may be invoked
  from non-main contexts when core's doctor harness eventually
  parallelizes. asyncio adds a runtime dependency for two checks.
  **Alternatives considered**: `concurrent.futures.ThreadPoolExecutor`
  (heavier than needed for one-shot timeouts — rejected).

- **Decision**: Auth bootstrap (initial OAuth consent flow) is
  intentionally out of scope; SKILL.md points users at the Google
  quickstart.
  **Why**: bootstrap requires a browser and `google-auth-oauthlib`'s
  `InstalledAppFlow.run_local_server`. Doing it correctly (port pick,
  redirect URI registration, headless detection) is a beadable chunk
  on its own and would dilute this issue.
  **Alternatives considered**: shipping `po gmail-bootstrap-auth` as a
  fourth command (deferred to a follow-up bead per plan §Risks).

- **Decision**: Lazy imports of `google.*` libs inside callable bodies
  (not at module top).
  **Why**: keeps `po list` / `po show` cheap and lets unit tests run
  the metadata-only paths without google-api-python-client installed.
  **Alternatives considered**: top-level imports + heavier test deps —
  rejected; symmetry with how `po-formulas` handles optional imports.

- **Decision**: One e2e test added to this rig
  (`tests/e2e/test_po_gmail_pack_install.py`) gated with
  `pytest.importorskip("po_gmail")`.
  **Why**: plan §"Test plan" explicitly calls for it; importorskip
  makes the rig's CI a no-op when the sibling pack isn't present.
  **Alternatives considered**: shelling out `po install --editable` in
  the test — would mutate the user's tool venv, rejected per "don't
  install side-effects in tests" convention.
