# Decision log — prefect-orchestration-3mw (build iter 1)

- **Decision**: Skipped `mcp-agent-mail` reservations + `po-mail` notifications.
  **Why**: The `mcp-agent-mail` MCP server is not installed in this
  environment (`ToolSearch select:mcp-agent-mail` returned no matches).
  The role prompt's reservation steps are best-effort; with the tool
  unavailable, file collisions are guarded only by git's "concurrent
  workers leave each other alone" hygiene. I scoped every `git add`
  to specific paths that match the plan's "Affected files" list, and
  `git status --short` after each commit showed only my own staged
  changes plus the pre-existing untracked / `M` entries from the
  initial git status (uv.lock, pyproject.toml, .planning/, …).
  **Alternatives considered**: ToolSearch with broader queries
  ("agent mail reservation") still surfaced no `mcp-agent-mail`
  schemas — so the tool genuinely isn't loadable here.

- **Decision**: Hardened `BeadsStore.get` / `BeadsStore.all` shape
  coercion via a private `_show_metadata()` helper rather than
  delegating to `_bd_show`.
  **Why**: `_bd_show` swallows non-zero exits and returns `None`,
  while `BeadsStore` historically uses `check=True` to surface bd
  failures loudly. The plan's "Why this shape" section called this
  out — preserving the exception contract is a behaviour-affecting
  decision. The new helper centralises shape coercion *inside*
  `BeadsStore` while keeping `check=True` intact.
  **Alternatives considered**: a single shared
  `_parse_show_output(stdout)` module-level helper. Rejected because
  `_bd_show`'s callers want None on JSON-decode failure, while
  `BeadsStore` should propagate the exception — different error
  contracts, deliberately.

- **Decision**: `_bd_available` left cwd-independent (deviation from
  literal AC1 wording).
  **Why**: `_bd_available` is `shutil.which("bd") is not None` —
  PATH-based, not cwd-based. Threading `rig_path` through it would
  be cargo-cult; bd's `which` lookup doesn't change per-cwd. The
  plan critique's nit #1 flagged this and explicitly endorsed the
  deviation, asking only that I call it out for the verifier.
  **Alternatives considered**: adding an unused `rig_path` kwarg for
  AC literalism. Rejected — adds API surface for no behavioural gain.

- **Decision**: Changed `BeadsStore.rig_path` to `Path | str | None`
  (rather than `Path | None` per the plan's "promote `BeadsStore`
  from a 1-field dataclass to carry `rig_path: Path | None = None`").
  **Why**: `auto_store` can be called with `rig_path=str(...)`
  (e.g. CLI paths come in as strings), and forcing callers to wrap
  in `Path()` is needless friction. `subprocess.run` accepts both;
  the `_cwd()` helper normalises via `str()` for explicitness.
  **Alternatives considered**: strict `Path` typing. Rejected — adds
  caller-side coercion with no runtime payoff.

- **Decision**: For the AC2 test (`build_registry` cwd propagation),
  let `flow_run.get_id()` return its natural test value (which is
  None, falling back to "local") rather than monkeypatching it.
  **Why**: Initial attempt monkeypatched `flow_run.get_id` to return
  `"abc12345-stable"`, then tried to monkeypatch `flow_run.tags` to
  bypass the Prefect tag-update side effect. `monkeypatch.setattr`
  for `flow_run.tags` triggers Prefect's lazy API resolver in its
  `getattr` backup pass, hitting the real Prefect server before the
  patch can apply. Letting `fr_id` fall through to "local" naturally
  skips the tag-update block (it's gated on `fr_id != "local"`) AND
  the URL composition in `prefect_run_url` (also gated on the
  sentinel). Side effect: `stamp_run_url_on_bead`'s shellout doesn't
  fire in this test — covered by a focused `test_stamp_run_url_on_bead_passes_cwd`
  that exercises the URL-present path in isolation.
  **Alternatives considered**: patching `prefect.client.orchestration.get_client`
  to raise so the body's try/except swallows it. More complex and
  doesn't actually solve the `flow_run.tags` getattr-backup problem.

- **Decision**: AC2 test tolerates both `Path` and `str` for the
  recorded `cwd` kwarg.
  **Why**: `_resolve_tmux_scope` (already-fixed earlier in
  `role_registry.py:222`) passes `cwd=rig_path_p` (a `PosixPath`),
  while the new edits pass `cwd=str(rig_path_p)`. `subprocess.run`
  accepts both. Normalising via `str()` in the assertion keeps the
  test honest without forcing a no-op cosmetic change to the existing
  `_resolve_tmux_scope` code.
  **Alternatives considered**: stringify everything in
  `_resolve_tmux_scope`. Rejected — out of scope for 3mw and risks
  churning a working codepath.

- **Decision**: Pack-side commits split across two `.git` repos.
  **Why**: Plan explicitly calls for the polyrepo edit (core
  `prefect-orchestration` repo + `software-dev/po-formulas` repo).
  Each commit message references `prefect-orchestration-3mw` so the
  link is greppable from either side. Used `git -C <pack-dir>` for
  the pack commit so the parent shell cwd stays in the rig.
  **Alternatives considered**: monolithic commit — not possible
  across separate `.git` ancestors.

- **Decision**: e2e test mocks `_dispatch_nodes` rather than running
  the full per-child `software_dev_full` actor-critic loop.
  **Why**: First attempt called `epic_run(...)` (not `.fn`) so
  Prefect would supply ephemeral runtime; that succeeded in
  discovering and dispatching all 3 children, but the per-child
  formula hit an unrelated pre-existing bug (`AgentSession.prompt()
  got an unexpected keyword argument 'fork_session'` in
  `parsing.py:64,71`). The 3mw fix is verified by "discovery walks
  the rig from outside-cwd and surfaces 3 children" — the
  actor-critic loop running in dry-run is a separate concern and is
  exercised by other tests. Mocking `_dispatch_nodes` (matching the
  pattern in `software-dev/po-formulas/tests/test_epic_discover_flags.py`)
  isolates the test to the cwd-plumbing surface, runs in 6s instead
  of 43s, and doesn't false-positive on the pre-existing
  `fork_session` bug.
  **Alternatives considered**: (a) call the real `epic_run` with
  full dispatch and tolerate any child failure → false-positive
  risk; (b) fix the `fork_session` bug as part of this PR → out of
  scope (separate beads issue would be more appropriate).

- **Decision**: Added a second negative-control e2e test
  (`test_epic_run_from_outside_cwd_without_rig_path_finds_zero_children`).
  **Why**: Documents the failure mode the bug describes (without
  `rig_path`, traversal returns [] from outside-cwd) and gives the
  rescue case a paired regression. If this test starts returning 3
  nodes in the future, that signals cwd inheritance is no longer
  the load-bearing failure mode (which would be fine but worth
  flagging).
  **Alternatives considered**: drop it — but the bug repro is the
  point of an e2e, and a single positive case can pass even if
  someone accidentally regresses cwd inheritance behaviour.
